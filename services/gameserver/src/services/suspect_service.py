"""Suspect-status lifecycle (WO-CMB-SUSPECT-LIFE-1, Max ruling 2026-07-10).

Canon: sw2102-docs/FEATURES/gameplay/ships.md:287-296 (the cargo-wreck
early-salvage grace mechanic that names the flag) + ADR-0061 S-V4 (team-
membership snapshot semantics) + DATA_MODELS/player.md's target schema for
``suspect_until`` / ``suspect_team_snapshot``.

Two writers flag a player Suspect today: early-window cargo-wreck salvage
(salvage_service.py) and contraband detection (contraband_service.py's
``_apply_heat``). Both now funnel through ``apply_suspect_event`` here
instead of hand-rolling the flag flip, so the lifecycle math (timer
extension + cumulative cap + snapshot-once + reputation hit) lives in
exactly ONE place.

NOT built here (explicitly out of this WO's scope, per the work order):
  * The auto-clear SWEEP's scheduler wiring. ``clear_expired_suspects``
    below is the pure, testable core (mirrors contract_generator.py's own
    "sync core + a scheduler wrapper owns SessionLocal/commit" split) --
    npc_scheduler_service.py is contended by other in-flight WOs, so the
    periodic-job registration is NOT added there. See this module's own
    report for the open item.

Fed-zone / Federation-Suspect immunity (attacking a live suspect fires no
``attack_innocent``) -- APPLIED (combat_service.py's ``attack_player``, the
same ``if`` check as the pre-existing WO-BL grey-flag exemption: ``if
attack_was_penalty_free or defender_is_live_suspect``). ``is_live_suspect``
below is the intended caller. ``attack_innocent`` carries no zone gating
anywhere in combat_service.py, so the suspension applies at the mechanic's
actual universal scope; canon's fed-space framing (police-forces.md) and the
nonexistent fight-back-cost mechanic remain pre-existing DOC-GAPs, not
resolved by this diff.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from src.models.player import Player

logger = logging.getLogger(__name__)

# ships.md:289 -- "+1 hour per event... capped at 4 hours total" from first acquisition.
SUSPECT_EXTENSION = timedelta(hours=1)
SUSPECT_MAX_CUMULATIVE = timedelta(hours=4)
# ships.md:294 -- "Permanent personal-reputation hit -- -25 ... per early-salvage event."
# Per the WO, contraband-heat suspect events get the identical treatment.
SUSPECT_REP_PENALTY = -25


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _team_snapshot(db: Session, player: Player) -> List[uuid.UUID]:
    """The flagged player's CURRENT team roster, frozen at first acquisition
    (ADR-0061 S-V4). Self-inclusive -- mirrors ADR-0060 G-F2's
    ``combat_lock_team_snapshot`` pattern (captures the whole team, not just
    "other" members); NO-CANON interpretation, since neither ships.md nor
    the ADR states self-inclusion explicitly. NULL-vs-empty per
    DATA_MODELS/player.md: this returns ``[]`` (never None) when the player
    has no team -- the caller decides when NULL (never-flagged) applies.

    Defensive like ``_apply_rep_penalty`` -- a query hiccup degrades to an
    empty snapshot rather than blocking the flag/timer mutation, which is
    the event's core, must-not-fail effect."""
    if player.team_id is None:
        return []
    try:
        rows = db.query(Player.id).filter(Player.team_id == player.team_id).all()
        return [row[0] for row in rows]
    except Exception:
        logger.warning("suspect-status team snapshot failed (non-fatal)", exc_info=True)
        return []


def _apply_rep_penalty(db: Session, player_id: uuid.UUID, reason: str) -> None:
    """-25 personal_reputation via the established PersonalReputationService
    idiom (mirrors contraband_service.py's ``_adjust_notoriety``). Defensive
    -- a reputation-service hiccup must never block the suspect-flagging
    event itself (the flag/timer/snapshot mutations already happened)."""
    try:
        from src.services.personal_reputation_service import PersonalReputationService
        PersonalReputationService(db).adjust_reputation(player_id, SUSPECT_REP_PENALTY, reason)
    except Exception:
        logger.warning("suspect-status reputation penalty failed (non-fatal)", exc_info=True)


def is_live_suspect(player: Player, *, now: Optional[datetime] = None) -> bool:
    """True iff ``player`` is EFFECTIVELY suspect right now -- checks
    ``suspect_until`` against the clock, not just the ``is_suspect`` boolean
    (which can be stale until the lazy auto-clear sweep runs). Called by
    combat_service.py's ``attack_player`` fed-zone-immunity check (see
    module doc-comment)."""
    now = _now(now)
    return bool(player.is_suspect) and player.suspect_until is not None and player.suspect_until > now


def apply_suspect_event(
    db: Session, player: Player, *, reason: str, now: Optional[datetime] = None
) -> bool:
    """Apply one suspect-flagging event to ``player`` (ships.md:287-296):

      * extends ``suspect_until`` by +1h, capped at ``suspect_declared_at``
        (the first-acquisition anchor) + 4h cumulative;
      * snapshots the player's current team roster ONCE, at first
        acquisition only -- a re-trigger within an already-active window
        never touches ``suspect_team_snapshot``;
      * applies the -25 personal-reputation hit, every event, first or not
        (ships.md:294 -- "per early-salvage event", no first-only carve-out).

    A stale ``is_suspect=True`` whose ``suspect_until`` has already elapsed
    (the lazy sweep hasn't run yet) is treated as a FRESH first acquisition,
    not a continuation -- see ``is_live_suspect``.

    NO flush, no commit -- pure in-memory mutation. Both callers
    (salvage_service.salvage_wreck / contraband_service._resolve_bust) own
    their own transaction boundary (salvage_service specifically stages
    everything and commits exactly ONCE at the end of the function -- an
    intermediate flush here would break that atomicity contract, pinned by
    test_salvage_turn_cost.py's TestAtomicity).

    Returns True iff this event was a FIRST acquisition (mirrors the
    pre-existing ``suspect_flagged`` return-value contract salvage_service
    callers already depend on), False for a re-trigger of an active window.
    """
    now = _now(now)
    first_acquisition = not is_live_suspect(player, now=now)

    if first_acquisition:
        player.is_suspect = True
        player.suspect_declared_at = now
        player.suspect_until = now + SUSPECT_EXTENSION
        player.suspect_team_snapshot = _team_snapshot(db, player)
    else:
        # suspect_declared_at is the anchor for the CUMULATIVE cap -- never
        # re-stamped on a re-trigger (that would let repeat offenses reset
        # the 4h ceiling indefinitely, defeating the cap's purpose).
        anchor = player.suspect_declared_at or now
        cap = anchor + SUSPECT_MAX_CUMULATIVE
        player.suspect_until = min(now + SUSPECT_EXTENSION, cap)
        # suspect_team_snapshot deliberately untouched -- frozen at first
        # acquisition only (ADR-0061 S-V4).

    _apply_rep_penalty(db, player.id, reason)
    return first_acquisition


def clear_expired_suspects(db: Session, *, now: Optional[datetime] = None) -> int:
    """Auto-clear sweep (ships.md:293 -- "Auto-clears at suspect_until --
    suspect_team_snapshot clears at the same time."). Clears is_suspect,
    suspect_until, suspect_team_snapshot, AND suspect_declared_at (the
    first-acquisition anchor this module owns) for every player whose
    suspect_until has elapsed.

    NO-CANON (flagged per the WO): does the 4h cumulative cap reset after a
    clear? This implementation says YES -- clearing suspect_declared_at
    means the NEXT suspect event starts a fresh acquisition (new anchor, new
    4h ceiling, new snapshot). The alternative (some rolling/decaying
    cumulative-offense counter that survives a clear) is not described
    anywhere in ships.md or ADR-0061 and would need its own ruling.

    FLUSH only -- the eventual scheduler wrapper (not built here, see this
    module's doc-comment) owns SessionLocal + commit, matching every other
    npc_scheduler_service.py sweep's split. Returns the count cleared.
    """
    now = _now(now)
    expired = (
        db.query(Player)
        .filter(
            Player.is_suspect.is_(True),
            Player.suspect_until.isnot(None),
            Player.suspect_until <= now,
        )
        .all()
    )
    for player in expired:
        player.is_suspect = False
        player.suspect_until = None
        player.suspect_team_snapshot = None
        player.suspect_declared_at = None

    if expired:
        db.flush()
    return len(expired)


__all__ = [
    "SUSPECT_EXTENSION",
    "SUSPECT_MAX_CUMULATIVE",
    "SUSPECT_REP_PENALTY",
    "is_live_suspect",
    "apply_suspect_event",
    "clear_expired_suspects",
]
