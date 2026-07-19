"""Team reputation service -- computed faction standing for player teams
(WO-RT-TEAM-REP). Canon: FEATURES/gameplay/factions-and-teams.md:392-399
("Team reputation with factions" -- three methods, 7-day method-switch
cooldown, "faction interactions treat the team as a unified diplomatic
entity").

Verified premise before building: TeamReputation (models/reputation.py:
111-127) and TeamReputationHandling (models/team.py:19) have ZERO readers/
writers anywhere outside models/__init__.py's re-export -- team_service.
create_team never creates a TeamReputation row, and nothing computes/reads/
switches a method today. This module is the FIRST writer. No migration --
the schema already exists (fully modeled, canon-backed scaffolding); this
module only reads/writes it.

Scope fence (explicit, per the WO): compute + expose + notify ONLY. Faction
interactions CONSUMING team standing (pricing/mission gates/etc. -- canon's
"faction interactions treat the team as a unified diplomatic entity") is
NOT built here -- flagged as an explicit follow-up, not silently implied.

[NO-CANON, model divergence -- flagged for DECISIONS, not silently
resolved]: BOTH ``Team.reputation_calculation_method`` (models/team.py:40)
AND ``TeamReputation.calculation_method`` (models/reputation.py:116) exist
-- two String(20) columns for the same concept, both defaulting to
"AVERAGE", both with zero readers/writers before this module. This service
treats ``TeamReputation.calculation_method`` as the single source of truth
(it lives on the reputation aggregate row this WO's brief names directly)
and never touches ``Team.reputation_calculation_method`` -- the duplicate
column is a genuine, unresolved model-level divergence that needs a real
decision (migrate one away, or wire a sync), not a silent pick.

Sync Session throughout; every function flushes, never commits -- the
caller (API route / eventual scheduler wrapper) owns the transaction
boundary, matching pirate_ecosystem_service.py / contract_service.py.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.faction import Faction
from src.models.reputation import Reputation, ReputationLevel, TeamReputation
from src.models.team import Team, TeamReputationHandling
from src.models.team_member import TeamMember

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors -- mirrors contract_service.py's ContractError / routes._raise_for
# idiom (a base error + typed subclasses the route layer isinstance-checks
# to pick an HTTP status).
# ---------------------------------------------------------------------------

class TeamReputationError(Exception):
    """Base error for this module."""


class TeamReputationPermissionError(TeamReputationError):
    """The actor is not authorized for this operation (not the team leader)."""


class TeamReputationCooldownError(TeamReputationError):
    """A method switch was attempted before the 7-day cooldown elapsed.
    ``retry_after`` is the earliest datetime a retry would succeed."""

    def __init__(self, message: str, retry_after: datetime):
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METHOD_SWITCH_COOLDOWN = timedelta(days=7)  # factions-and-teams.md:399

# [NO-CANON]: canon says nothing about recalculation CADENCE, only that a
# standing exists and updates. Daily is a reasonable default -- frequent
# enough that a member's reputation swing is felt within a day, coarse
# enough that a 4-member team's faction standings aren't churning every
# tick. Flagged for DECISIONS.
RECALCULATION_INTERVAL = timedelta(days=1)

# Advisory-lock key mnemonic, pre-declared for the future scheduler wiring
# (npc_scheduler_service.py is HELD this wave -- see the WO). Packed
# exactly like npc_scheduler_service._mnemonic_lock_key('GCRB'/'PRSW'):
# four ASCII bytes, big-endian, always non-negative and well inside the
# signed-63-bit pg_try_advisory_xact_lock(bigint) range. 'TREP' = Team
# REPutation. NOT referenced by any live pg_try_advisory_xact_lock call
# yet -- that's the scheduler-wiring step, out of this module's reach
# while the file is held. Defined here so the eventual wiring is "one
# import away" (`from src.services.team_reputation_service import
# TEAM_REPUTATION_SWEEP_LOCK_KEY, sweep_due_team_reputations`).
TEAM_REPUTATION_SWEEP_LOCK_KEY = int.from_bytes(b"TREP", "big")

# Mirrors faction_service.FactionService._calculate_reputation_level's
# EXACT thresholds (a private instance method on an async class this sync
# module does not instantiate) -- duplicated here as a pure function
# rather than left unreconciled; if that table ever changes, this one
# must change with it (flagged, not a shared import, since the source is
# a private method on a class this module has no reason to instantiate).
_LEVEL_THRESHOLDS = (
    (700, ReputationLevel.EXALTED),
    (600, ReputationLevel.REVERED),
    (500, ReputationLevel.HONORED),
    (400, ReputationLevel.VALUED),
    (300, ReputationLevel.RESPECTED),
    (200, ReputationLevel.TRUSTED),
    (100, ReputationLevel.ACKNOWLEDGED),
    (50, ReputationLevel.RECOGNIZED),
    (-50, ReputationLevel.NEUTRAL),
    (-100, ReputationLevel.QUESTIONABLE),
    (-200, ReputationLevel.SUSPICIOUS),
    (-300, ReputationLevel.UNTRUSTWORTHY),
    (-400, ReputationLevel.SMUGGLER),
    (-500, ReputationLevel.PIRATE),
    (-600, ReputationLevel.OUTLAW),
    (-700, ReputationLevel.CRIMINAL),
)


def _reputation_level_for(value: int) -> ReputationLevel:
    """Pure core -- the same threshold table as faction_service's private
    method, walked high-to-low; below the lowest bucket falls to
    PUBLIC_ENEMY."""
    for floor, level in _LEVEL_THRESHOLDS:
        if value >= floor:
            return level
    return ReputationLevel.PUBLIC_ENEMY


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# TeamReputation lazy-init (this module is the FIRST writer -- see the
# module docstring's verified premise)
# ---------------------------------------------------------------------------

def _get_or_create_team_reputation(
    db: Session, team: Team, *, now: Optional[datetime] = None
) -> TeamReputation:
    """Lazy-init: a fresh row starts on the default AVERAGE method with
    ``next_recalculation`` due IMMEDIATELY (== now) -- see
    get_team_reputation's own docstring for why that matters (it makes
    the very first read of a new team's standings self-heal into a real
    recalculation instead of exposing an empty snapshot indefinitely
    while the scheduler sweep is held)."""
    existing = db.query(TeamReputation).filter(TeamReputation.team_id == team.id).first()
    if existing is not None:
        return existing
    now = _now(now)
    team_rep = TeamReputation(
        id=uuid.uuid4(),
        team_id=team.id,
        calculation_method=TeamReputationHandling.AVERAGE.value,
        faction_reputation={},
        history=[],
        last_recalculated=now,
        next_recalculation=now,
        pending_notifications=[],
    )
    db.add(team_rep)
    db.flush()
    return team_rep


# ---------------------------------------------------------------------------
# Aggregation core (pure where possible)
# ---------------------------------------------------------------------------

def _member_values_for_faction(
    db: Session, member_player_ids: List[uuid.UUID], faction_id: uuid.UUID
) -> List[int]:
    """Each member's current_value for this faction; a member with no
    Reputation row for it contributes NEUTRAL (0) -- mirrors Reputation's
    own model default rather than excluding them from the average."""
    if not member_player_ids:
        return []
    rows = {
        r.player_id: r.current_value
        for r in db.query(Reputation).filter(
            Reputation.player_id.in_(member_player_ids),
            Reputation.faction_id == faction_id,
        ).all()
    }
    return [rows.get(pid, 0) for pid in member_player_ids]


def _aggregate_value(method: str, values: List[int], leader_value: Optional[int]) -> int:
    """Pure core: apply AVERAGE/LOWEST/LEADER to one faction's member
    values. An empty team (zero members) degrades to NEUTRAL (0) --
    [NO-CANON], there's no sensible standing for a team with no members.
    LEADER with no leader Reputation row for this faction -> NEUTRAL (0),
    per the WO's explicit instruction -- ``leader_value`` is already
    resolved to that default by the caller before reaching here."""
    if method == TeamReputationHandling.LEADER.value:
        return leader_value if leader_value is not None else 0
    if not values:
        return 0
    if method == TeamReputationHandling.LOWEST.value:
        return min(values)
    # AVERAGE (default, and the safety fallback for any unrecognized
    # method value that somehow reached here despite switch_method's own
    # enum validation).
    return round(sum(values) / len(values))


# ---------------------------------------------------------------------------
# Realtime telemetry -- [NO-CANON] `team_reputation_changed`. Canon states
# team standing exists and is diplomatically consumed (:399) but names no
# realtime event/shape for a tier change.
# ---------------------------------------------------------------------------

def _broadcast_team_event(team_id: uuid.UUID, payload: Dict[str, Any]) -> None:
    """Best-effort realtime broadcast to the team room. Mirrors the
    established idiom EXACTLY (combat_service._emit_teammate_under_attack /
    pirate_ecosystem_service._broadcast_pirate_event): lazy import the
    connection_manager SINGLETON -- never instantiate ConnectionManager()
    (tests/unit/test_ws_singleton_wiring.py's AST scan enforces exactly
    ONE ConnectionManager() call across all of src/, in
    services/websocket_service.py; this module must never add a second) --
    grab the running loop, loop.create_task() so the send never blocks,
    swallow every failure so telemetry can never break a recalculation."""
    try:
        import asyncio

        from src.services.websocket_service import connection_manager

        asyncio.get_running_loop().create_task(
            connection_manager.broadcast_to_team(str(team_id), payload)
        )
    except RuntimeError:
        pass  # no running loop -- sync/worker context; nothing polls this today
    except Exception:
        logger.exception(
            "team reputation telemetry broadcast failed for type=%s (non-fatal)",
            payload.get("type"),
        )


def _emit_team_reputation_changed_event(
    team: Team, change: Dict[str, Any], *, method: str, now: Optional[datetime] = None
) -> None:
    """Fired once per faction whose tier changed this recalculation
    (mirrors faction_service.update_reputation's per-faction
    'reputation_changed' personal-message pattern, team-scoped here via
    broadcast_to_team instead of a personal message)."""
    _broadcast_team_event(team.id, {
        "type": "team_reputation_changed",
        "team_id": str(team.id),
        "method": method,
        "timestamp": _iso(_now(now)),
        **change,
    })


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recalculate_team(db: Session, team: Team, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Recompute every faction's standing for ``team`` per its configured
    method (factions-and-teams.md:392-399), persist the snapshot, and
    notify on any tier change. FLUSH-ONLY -- caller commits.

    [NO-CANON]: iterates the FULL Faction catalog every call (not just
    factions a member has interacted with) so ``faction_reputation``
    always carries a complete, predictable shape -- a faction nobody has
    touched reads as NEUTRAL, matching Reputation's own per-player
    default rather than being absent from the dict entirely.
    """
    now = _now(now)
    team_rep = _get_or_create_team_reputation(db, team, now=now)
    method = team_rep.calculation_method

    member_ids = [
        pid for (pid,) in
        db.query(TeamMember.player_id).filter(TeamMember.team_id == team.id).all()
    ]
    leader_id = team.leader_id

    previous = dict(team_rep.faction_reputation or {})
    standings: Dict[str, Any] = {}
    changed: List[Dict[str, Any]] = []

    for faction in db.query(Faction).all():
        faction_key = str(faction.id)
        values = _member_values_for_faction(db, member_ids, faction.id)

        leader_value = None
        if leader_id is not None:
            leader_row = (
                db.query(Reputation)
                .filter(Reputation.player_id == leader_id, Reputation.faction_id == faction.id)
                .first()
            )
            leader_value = leader_row.current_value if leader_row is not None else 0

        value = _aggregate_value(method, values, leader_value)
        level = _reputation_level_for(value)

        prior_entry = previous.get(faction_key)
        prior_level = prior_entry.get("level") if prior_entry else None

        standings[faction_key] = {
            "faction_id": faction_key,
            "faction_name": faction.name,
            "value": value,
            "level": level.value,
        }

        # A tier change only counts as an event when a PRIOR snapshot
        # existed and disagreed -- the very first computation for a
        # faction is never itself a "change" worth notifying about.
        if prior_entry is not None and prior_level != level.value:
            changed.append({
                "faction_id": faction_key,
                "faction_name": faction.name,
                "old_value": prior_entry.get("value"),
                "new_value": value,
                "old_level": prior_level,
                "new_level": level.value,
            })

    team_rep.faction_reputation = standings
    flag_modified(team_rep, "faction_reputation")

    history = list(team_rep.history or [])
    history.append({
        "kind": "recalculation",
        "timestamp": _iso(now),
        "method": method,
        "standings": standings,
    })
    team_rep.history = history
    flag_modified(team_rep, "history")

    if changed:
        notifications = list(team_rep.pending_notifications or [])
        for c in changed:
            notifications.append({**c, "timestamp": _iso(now)})
        team_rep.pending_notifications = notifications
        flag_modified(team_rep, "pending_notifications")

    team_rep.last_recalculated = now
    team_rep.next_recalculation = now + RECALCULATION_INTERVAL
    db.flush()

    for c in changed:
        _emit_team_reputation_changed_event(team, c, method=method, now=now)

    return {
        "team_id": str(team.id),
        "method": method,
        "standings": standings,
        "changed": changed,
        "last_recalculated": _iso(now),
        "next_recalculation": _iso(team_rep.next_recalculation),
    }


def get_team_reputation(db: Session, team: Team, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read path for ``GET /teams/{id}/reputation``. Lazily creates the
    row for a team that's never been computed, and recalculates INLINE if
    the stored snapshot is past its ``next_recalculation`` due date.

    [NO-CANON, pragmatic interim]: the scheduler sweep this WO's sync core
    targets (``sweep_due_team_reputations``) is HELD pending
    npc_scheduler_service.py freeing up -- without this read-triggers-
    refresh-if-due fallback, a fresh team's standings would sit
    empty/stale indefinitely with no sweep to populate them. A freshly
    created row's ``next_recalculation`` is set to its own creation
    timestamp (see ``_get_or_create_team_reputation``), so a team's very
    first read always self-heals into a real computation. Once the sweep
    is wired, this fallback becomes a rare cold-start path instead of the
    primary trigger, not a redundant one -- it stays as defense-in-depth.
    """
    now = _now(now)
    team_rep = _get_or_create_team_reputation(db, team, now=now)
    if team_rep.next_recalculation <= now:
        return recalculate_team(db, team, now=now)
    return {
        "team_id": str(team.id),
        "method": team_rep.calculation_method,
        "standings": team_rep.faction_reputation or {},
        "last_recalculated": _iso(team_rep.last_recalculated),
        "next_recalculation": _iso(team_rep.next_recalculation),
        "pending_notifications": list(team_rep.pending_notifications or []),
    }


def _last_method_switch_at(team_rep: TeamReputation) -> Optional[datetime]:
    """Scans history for the most recent 'method_switch' entry.

    [NO-CANON, flagged]: the 7-day cooldown (:399) has no dedicated "last
    method change" column on TeamReputation, and this WO explicitly
    avoids a migration to protect the existing canon-backed schema --
    resolved by tagging switch events within the existing ``history``
    JSONB and scanning for the latest tag. A dedicated column would be
    O(1) instead of O(history) and cleaner; flagged for DECISIONS."""
    latest = None
    for entry in team_rep.history or []:
        if entry.get("kind") != "method_switch":
            continue
        ts = entry.get("timestamp")
        if not ts:
            continue
        parsed = datetime.fromisoformat(ts)
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def switch_method(
    db: Session, team: Team, method: Any, actor_player_id: uuid.UUID,
    *, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Leader-only method switch with the canon 7-day cooldown
    (factions-and-teams.md:399), forcing an immediate recalculation on
    success. ``method`` accepts either a ``TeamReputationHandling`` member
    or its raw string value.

    Raises ``TeamReputationPermissionError`` (actor isn't the leader),
    ``TeamReputationCooldownError`` (within-cooldown -- carries
    ``retry_after``), or the base ``TeamReputationError`` (unknown method
    string)."""
    now = _now(now)
    resolved_method = method.value if isinstance(method, TeamReputationHandling) else str(method)
    try:
        TeamReputationHandling(resolved_method)
    except ValueError:
        raise TeamReputationError(f"unknown reputation method: {resolved_method!r}") from None

    if team.leader_id != actor_player_id:
        raise TeamReputationPermissionError("only the team leader can switch the reputation method")

    team_rep = _get_or_create_team_reputation(db, team, now=now)

    last_switch = _last_method_switch_at(team_rep)
    if last_switch is not None:
        earliest_allowed = last_switch + METHOD_SWITCH_COOLDOWN
        if now < earliest_allowed:
            raise TeamReputationCooldownError(
                "reputation method switch is on a 7-day cooldown", retry_after=earliest_allowed,
            )

    team_rep.calculation_method = resolved_method
    history = list(team_rep.history or [])
    history.append({
        "kind": "method_switch",
        "timestamp": _iso(now),
        "method": resolved_method,
        "actor_player_id": str(actor_player_id),
    })
    team_rep.history = history
    flag_modified(team_rep, "history")
    db.flush()

    return recalculate_team(db, team, now=now)


# ---------------------------------------------------------------------------
# Scheduler sweep -- HELD. npc_scheduler_service.py is mid-wave this
# session; this is the tested sync core only. Wiring it behind
# TEAM_REPUTATION_SWEEP_LOCK_KEY (pg_try_advisory_xact_lock) into the
# scheduler's tick loop is the reported open item, not built here.
# ---------------------------------------------------------------------------

def sweep_due_team_reputations(db: Session, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Recalculate every team whose ``TeamReputation.next_recalculation``
    is due. FLUSH-ONLY -- the eventual scheduler wrapper owns SessionLocal
    + commit, matching every other npc_scheduler_service.py sweep's split
    (``contract_service.sweep_expired_contracts`` /
    ``suspect_service.clear_expired_suspects``)."""
    now = _now(now)
    due_team_ids = [
        tid for (tid,) in
        db.query(TeamReputation.team_id).filter(TeamReputation.next_recalculation <= now).all()
    ]
    recalculated = 0
    for team_id in due_team_ids:
        team = db.query(Team).filter(Team.id == team_id).first()
        if team is None:
            continue
        recalculate_team(db, team, now=now)
        recalculated += 1
    return {"due": len(due_team_ids), "recalculated": recalculated}


__all__ = [
    "TeamReputationError",
    "TeamReputationPermissionError",
    "TeamReputationCooldownError",
    "METHOD_SWITCH_COOLDOWN",
    "RECALCULATION_INTERVAL",
    "TEAM_REPUTATION_SWEEP_LOCK_KEY",
    "recalculate_team",
    "get_team_reputation",
    "switch_method",
    "sweep_due_team_reputations",
]
