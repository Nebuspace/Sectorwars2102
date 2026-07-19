"""Weekly decay + reputation-drip sweeps (WO-QUALITY-techdebt-scheduler-split).

Weekly personal/faction/ARIA-relationship inactivity decay, the sustained-
reputation drip (factions-and-teams.md ongoing-state drips), and the
team-reputation recalculation sweep.

Moved verbatim from the old ``npc_scheduler_service.py``.
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.faction import FactionType
from src.models.player import Player
from src.services.faction_service import apply_faction_rep_delta

from src.services.scheduler._common import (
    _WEEKLY_DECAY_STATE_KEY,
    _TEAM_REPUTATION_SWEEP_STATE_KEY,
    TEAM_REPUTATION_SWEEP_SECONDS,
    _WEEKLY_DECAY_LOCK_KEY,
    _SUSTAINED_DRIP_LOCK_KEY,
    canonical_day_number,
    canonical_week_number,
    _sweep_due_and_advance,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weekly maintenance — reputation / relationship decay
# ---------------------------------------------------------------------------

def _select_decay_candidate_ids(db: Session) -> List[Any]:
    """Player ids worth running decay for. Decay only ever moves values toward
    a neutral baseline, so a player whose personal_reputation is already 0,
    whose faction reps are all neutral, and whose ARIA relationship is at the
    floor has nothing to decay — but the called services are individually cheap
    and idempotent (each returns a no-op for a baseline player), so the simple,
    robust choice is to run every real player. We exclude only soft-deactivated
    accounts."""
    rows = (
        db.query(Player.id)
        .filter(Player.is_active.is_(True))
        .all()
    )
    return [r[0] for r in rows]


def _canonical_days_inactive(player: Player, now: datetime) -> int:
    """Canonical days since the player last logged in (>=0). A player who has
    never logged in (last_game_login NULL) is treated as 0 days inactive — we
    do not punish a brand-new account on its first scheduled week."""
    if player.last_game_login is None:
        return 0
    hours = game_time.canonical_hours_since(player.last_game_login, now)
    return max(0, int(hours // 24))


# Faction inactivity-decay parameters — mirrored verbatim from
# FactionService.apply_reputation_decay so the inline sync reimplementation
# applies IDENTICAL decay (we cannot await the async method here without
# poisoning the shared async connection pool — see _run_weekly_decay_sync).
_FACTION_DECAY_INACTIVE_DAYS = 30   # only reps idle > 30 days decay
_FACTION_DECAY_NEUTRAL_BAND = 100   # reps within [-100, +100] never decay
_FACTION_DECAY_MAX_PER_CALL = 50    # absolute cap on decay applied per rep/call


def _apply_personal_decay_sync(db: Session, player_ids: List[Any]) -> int:
    """Personal-reputation weekly decay (SYNC service, sync session). Decays
    each player's personal_reputation toward 0 by 5/week; counts the ones that
    actually moved.

    NOTE: this runs inside the caller's SINGLE atomic weekly transaction, so it
    does NOT catch/rollback per player — a per-row rollback would discard the
    other players' already-applied decays AND the week anchor. Any error
    propagates to _run_weekly_decay_sync, which rolls the whole week back and
    retries next wake (so the week is never silently half-applied or skipped)."""
    from src.services.personal_reputation_service import PersonalReputationService

    svc = PersonalReputationService(db)
    decayed = 0
    for pid in player_ids:
        result = svc.apply_weekly_decay(pid)
        if result.get("decayed"):
            decayed += 1
    db.flush()
    return decayed


def _apply_faction_decay_sync(db: Session, player_ids: List[Any]) -> int:
    """Faction reputation inactivity-decay — SYNC reimplementation on the work
    session.

    FactionService.apply_reputation_decay is declared ``async def``; even though
    its body is pure sync ORM, calling it would force an ``asyncio.run`` /
    AsyncSession path through the shared async engine, whose connections, if
    created inside a throwaway event loop, get returned to the global pool bound
    to a dead loop and later raise "Event loop is closed" in unrelated request
    handlers. So we replicate its decay logic here against the sync session and
    reuse only its STATELESS recalc helpers (pure functions over an int — no DB,
    no loop). The thresholds/cap are kept in sync via the constants above.

    Counts the players that had >=1 faction reputation decayed. Per-player
    failure is isolated; the work session is committed by the caller."""
    from src.models.reputation import Reputation
    from src.services.faction_service import FactionService

    helpers = FactionService(db)  # used ONLY for its pure recalc helpers
    now = datetime.utcnow()  # matches the async method's naive-UTC comparison
    affected_players = 0

    # Runs inside the caller's single atomic weekly transaction — no per-row
    # rollback (that would corrupt the shared txn); errors propagate to
    # _run_weekly_decay_sync, which rolls the whole week back and retries.
    for pid in player_ids:
        reputations = (
            db.query(Reputation)
            .filter(Reputation.player_id == pid)
            .all()
        )
        player_changed = False
        for rep in reputations:
            if rep.decay_paused or rep.is_locked:
                continue
            if -_FACTION_DECAY_NEUTRAL_BAND <= rep.current_value <= _FACTION_DECAY_NEUTRAL_BAND:
                continue
            last = (
                rep.last_updated.replace(tzinfo=None)
                if rep.last_updated and rep.last_updated.tzinfo
                else rep.last_updated
            )
            if last is None:
                continue
            inactive_days = (now - last).days
            if inactive_days <= _FACTION_DECAY_INACTIVE_DAYS:
                continue

            decay_amount = min(
                inactive_days - _FACTION_DECAY_INACTIVE_DAYS,
                _FACTION_DECAY_MAX_PER_CALL,
            )
            old_value = rep.current_value
            if rep.current_value > _FACTION_DECAY_NEUTRAL_BAND:
                rep.current_value = max(
                    _FACTION_DECAY_NEUTRAL_BAND, rep.current_value - decay_amount
                )
            elif rep.current_value < -_FACTION_DECAY_NEUTRAL_BAND:
                rep.current_value = min(
                    -_FACTION_DECAY_NEUTRAL_BAND, rep.current_value + decay_amount
                )

            if rep.current_value != old_value:
                rep.current_level = helpers._calculate_reputation_level(rep.current_value)
                rep.title = helpers._get_reputation_title(rep.current_level)
                rep.trade_modifier = helpers._calculate_trade_modifier(rep.current_value)
                rep.port_access_level = helpers._calculate_port_access_level(rep.current_value)
                rep.combat_response = helpers._calculate_combat_response(rep.current_value)
                rep.history = (rep.history or []) + [{
                    "timestamp": now.isoformat(),
                    "old_value": old_value,
                    "new_value": rep.current_value,
                    "change": rep.current_value - old_value,
                    "reason": f"Inactivity decay ({inactive_days - _FACTION_DECAY_INACTIVE_DAYS} days idle)",
                }]
                player_changed = True
        if player_changed:
            affected_players += 1
    db.flush()
    return affected_players


def _apply_aria_decay_sync(db: Session, player_ids: List[Any], now: datetime) -> int:
    """ARIA relationship inactivity-decay — SYNC reimplementation on the work
    session.

    AriaPersonalIntelligenceService.apply_inactivity_decay is genuinely async
    (it takes an AsyncSession), but the LOGIC is pure arithmetic:
    ``aria_relationship_score`` loses 1 point per inactive day, floored at 0. We
    reproduce that here on the sync session — no AsyncSession, no event loop —
    so nothing can poison the shared async pool. ``days_inactive`` is canonical
    days since last_game_login (a no-op at 0 or when the score is already 0).

    Counts the players whose score actually moved. Runs inside the caller's
    single atomic weekly transaction (no per-row rollback); errors propagate to
    _run_weekly_decay_sync, which rolls the whole week back and retries."""
    decayed = 0
    for pid in player_ids:
        player = db.query(Player).filter(Player.id == pid).first()
        if player is None:
            continue
        days = _canonical_days_inactive(player, now)
        if days <= 0:
            continue
        score = player.aria_relationship_score or 0
        decay = min(days, score)
        if decay <= 0:
            continue
        player.aria_relationship_score = max(0, score - decay)
        decayed += 1
    db.flush()
    return decayed


def _run_weekly_decay_sync() -> Dict[str, int]:
    """Weekly reputation/relationship maintenance — FULLY SYNCHRONOUS, self-gated
    on a DURABLE canonical-week anchor in ``Galaxy.state`` so restarts neither
    skip nor double a week.

    No asyncio / AsyncSession is used anywhere: all three decays (personal,
    faction, ARIA) run synchronously on a SINGLE work session inside the
    advisory-locked transaction, and the durable week anchor is advanced in that
    SAME transaction. This guarantees atomicity — the week is marked done iff
    every decay batch committed — and avoids the async-pool poisoning that an
    ``asyncio.run`` bridge over the shared async engine would cause ("Event loop
    is closed" in later, unrelated request handlers).

    xact-advisory-lock-gated like the other scheduler work (one instance per
    week). If any decay batch raises, the whole transaction rolls back, the
    anchor is NOT advanced, and the job retries next wake (decay is
    idempotent/convergent — at worst one extra 5-point personal step, clamped
    toward zero).

    Returns {personal, faction, aria, week} (all zero + week=-1 when the week is
    not yet due / lock held elsewhere)."""
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy

    this_week = canonical_week_number()
    not_due = {"personal": 0, "faction": 0, "aria": 0, "week": -1}

    # Single locked transaction: lock + anchor read + all decays + anchor
    # advance all commit together (or roll back together).
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _WEEKLY_DECAY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_due

        # Stable anchor row: the OLDEST galaxy (created_at.asc()). A dev
        # re-bootstrap creates a NEWER galaxy; keying off the newest would reset
        # the anchor and double-fire the global decay, so we pin to the oldest.
        galaxy = (
            db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
        )
        if galaxy is None:
            return not_due
        state = dict(galaxy.state or {})
        last_week = state.get(_WEEKLY_DECAY_STATE_KEY)
        if last_week is not None and int(last_week) >= this_week:
            return not_due

        player_ids = _select_decay_candidate_ids(db)
        now = datetime.now(UTC)

        # All three decays on the SAME session — any raise propagates to the
        # outer except, rolling back everything (including the anchor advance).
        personal = _apply_personal_decay_sync(db, player_ids)
        faction = _apply_faction_decay_sync(db, player_ids)
        aria = _apply_aria_decay_sync(db, player_ids, now)

        # Advance the durable anchor in the SAME transaction as the decays.
        state = dict(galaxy.state or {})
        state[_WEEKLY_DECAY_STATE_KEY] = this_week
        galaxy.state = state
        flag_modified(galaxy, "state")
        db.commit()  # commits decays + anchor atomically AND releases the lock

        result = {
            "personal": personal,
            "faction": faction,
            "aria": aria,
            "week": this_week,
        }
        logger.info(
            "weekly-decay: canonical week %d — personal=%d faction=%d aria=%d "
            "(over %d player(s))",
            this_week, personal, faction, aria, len(player_ids),
        )
        return result
    except Exception:
        # Any failure: roll back EVERYTHING (decays + anchor) so the week is not
        # silently skipped — it will be retried on the next due wake.
        logger.exception("weekly-decay: batch failed — week not advanced")
        db.rollback()
        return not_due
    finally:
        db.close()




# ---------------------------------------------------------------------------
# Sustained-reputation drips (factions-and-teams.md:220-230 "Ongoing-state
# drip mechanics", WO-PROG-SUSTAINED-DRIPS)
# ---------------------------------------------------------------------------
#
# Of the six ongoing-state drip rows in that canon table, only the two below
# are BUILDABLE today (personal_reputation is a live Player column with no
# unbuilt dependency). The other four are BLOCKED and intentionally NOT
# scaffolded here: ship-skin wearing (no skin-ownership/equip system yet),
# the contraband-per-sector-hop drip (no hop-tracking hook), and the
# Wanted-status-docked-at-a-Fringe-port drip (no live docked-location x
# wanted-status join exists on the scheduler side).
#
# Personal-reputation band thresholds — CANON numbers (factions-and-
# teams.md:229-230), matching personal_reputation_service.REPUTATION_TIERS'
# own Heroic (250-499) / Outlaw (-499..-250) tier boundaries. "Heroic+" reads
# as personal_reputation >= 250 (covers Heroic AND Legendary); "Outlaw+"
# reads as personal_reputation <= -250 (covers Outlaw, Criminal, AND
# Villain) — exactly the canon table's ">= +250" / "<= -250" wording, not a
# single-tier match.
SUSTAINED_HEROIC_THRESHOLD = 250   # personal_reputation >= this -> "heroic" band
SUSTAINED_OUTLAW_THRESHOLD = -250  # personal_reputation <= this -> "outlaw" band
# CANON: 7+ CANONICAL days sustained before the drip starts (factions-and-
# teams.md:229-230) — see apply_sustained_reputation_drip's docstring for the
# wall-vs-canonical time-domain reasoning.
SUSTAINED_DRIP_DAYS_REQUIRED = 7
# CANON per-canonical-day drip magnitudes once sustained, applied to the
# guardian faction that distrusts the extreme, sustained alignment.
SUSTAINED_HEROIC_DRIP_DELTA = -5   # Fringe Alliance (FactionType.OUTLAWS)
SUSTAINED_OUTLAW_DRIP_DELTA = -2   # Mercantile Guild (FactionType.MERCHANTS)

# Durable per-player anchor key on Player.settings (additive JSONB, NO
# migration, NO new table — NO-CANON key shape, documented here since canon
# only specifies the drip EFFECT, not its persistence mechanism). Value
# shape: {"band": "heroic" | "outlaw", "since_day": <canonical_day_number()
# int the band was entered>, "last_drip_day": <canonical_day_number() int of
# the most recently applied drip, or None>}. Both day fields are CANONICAL-
# DAY INTEGERS (not ISO timestamps) so the 7-day comparison is a plain
# integer subtraction — consistent with every other durable day-anchor in
# this file (Player.settings[system_bounty_pot_period], Galaxy.
# state[treasury_reconciliation_last_day]) and immune to timezone/parsing
# edge cases an ISO string invites.
_SUSTAINED_TIER_SETTINGS_KEY = "sustained_tier"


def apply_sustained_reputation_drip(
    db: Session, player: Player, today: int
) -> Optional[str]:
    """Pure per-player state-machine step for the sustained-reputation drip.
    Mutates ``player.settings`` (dict-copy reassignment + flag_modified — the
    FL-INTEGRITY pattern in emergent_reputation_service._store_throttle_
    bucket) and, when a drip is actually due, calls the module-level
    ``apply_faction_rep_delta`` — NEVER a direct Reputation write, so a
    caller-supplied fake/spy on that name observes every drip without this
    function needing to know anything about the Reputation/Faction schema.

    ``apply_faction_rep_delta`` is the SYNC, flush-only primitive built for
    in-transaction penalty hooks outside a request/route (combat_service,
    mining_service, contraband_service, distress_service all call it the
    same way) — the only surface usable from THIS sync scheduler sweep.
    ``FactionService.update_reputation`` is async, commits internally
    mid-transaction, and fires websocket sends; calling it here would
    double-commit and break the sweep's per-row transaction exactly the way
    calling it from combat_service's sync path would (see apply_faction_rep_
    delta's own docstring). The CALLER owns ``player``'s row lock and the
    transaction commit — this function only flushes (via apply_faction_rep_
    delta) and reassigns the JSONB attribute; it never commits.

    TIME DOMAIN: ``today`` MUST be a CANONICAL day index
    (``canonical_day_number()``), not a wall-clock UTC day. This drip is a
    simulation-time consequence of a player's reputation STATE persisting,
    not a real-world-engagement signal — unlike the daily rep-stipend
    faucet's deliberate wall-clock UTC gate (which rewards an actual login
    THAT calendar day and would make no sense accelerated), a sustained-
    reputation drip belongs in the same time domain as every other
    reputation/economy mechanic keyed off this scheduler's canonical clock:
    _run_weekly_decay_sync's canonical-week decay, _run_bounty_accrual_
    sweep_sync's canonical-day pot growth, and the ongoing-state drip
    table's own intro ("per-tick updates are accumulated and flushed hourly
    by the wrapper" — already a simulation-clock cadence, not a real-time
    one). Dev-observable at GAME_TIME_SCALE=144: a canonical day elapses
    every ~10 wall-clock minutes, so the 7-canonical-day sustained threshold
    resolves in ~70 wall-clock minutes — the same canonical-week span
    _run_weekly_decay_sync's own docstring cites.

    STATE MACHINE:
      * current personal_reputation resolves to band "heroic" (>= +250),
        "outlaw" (<= -250), or None (back in the middle).
      * band is None: if a tracker exists, CLEAR it (pop the settings key) —
        canon's "sustained" wording carries no partial credit, so dropping
        out of range and back in later starts the clock over. If no tracker
        exists, a clean no-op (no write).
      * band is set and (no tracker exists, OR the tracker's band differs,
        OR the tracker's since_day is corrupted/unparsable/in the future):
        START a fresh tracker — since_day = today, last_drip_day = None, no
        drip yet. A flip straight from heroic to outlaw (or vice versa) is
        treated as leaving the old band (its clock resets) and entering the
        new one (its own clock starts at zero) — canon has no notion of a
        combined clock across the two mutually-exclusive bands.
      * band matches the tracker and (today - since_day) < 7: not yet
        sustained — no write (nothing changed; since_day is preserved as-is
        by simply not touching settings).
      * band matches, elapsed >= 7, and last_drip_day >= today: ALREADY
        dripped this canonical day — idempotent no-op, no write, no second
        call to apply_faction_rep_delta (a restart or a second sweep wake
        within the same canonical day never double-drips).
      * band matches, elapsed >= 7, and last_drip_day < today (or None):
        APPLY today's drip via apply_faction_rep_delta, then persist
        last_drip_day = today (since_day is preserved, NOT reset — the
        sustained clock keeps running for as long as the band holds, so a
        player who stays Heroic for 30 days drips every one of the 23 days
        past the 7-day threshold).

    Returns "heroic" or "outlaw" when a drip was actually applied this call,
    else None (every other branch above, including a fresh/cleared/reset
    tracker or a not-yet-sustained/already-dripped no-op)."""
    rep = player.personal_reputation or 0
    if rep >= SUSTAINED_HEROIC_THRESHOLD:
        band = "heroic"
    elif rep <= SUSTAINED_OUTLAW_THRESHOLD:
        band = "outlaw"
    else:
        band = None

    settings = player.settings if isinstance(player.settings, dict) else {}
    tracker = settings.get(_SUSTAINED_TIER_SETTINGS_KEY)
    if not isinstance(tracker, dict):
        tracker = None

    def _write(new_tracker: Optional[Dict[str, Any]]) -> None:
        new_settings = dict(settings)
        if new_tracker is None:
            new_settings.pop(_SUSTAINED_TIER_SETTINGS_KEY, None)
        else:
            new_settings[_SUSTAINED_TIER_SETTINGS_KEY] = new_tracker
        player.settings = new_settings
        flag_modified(player, "settings")

    if band is None:
        if tracker is not None:
            _write(None)  # left the sustained range -- clock resets to zero
        return None

    since_day: Optional[int] = None
    if tracker is not None and tracker.get("band") == band:
        try:
            candidate_since = int(tracker.get("since_day"))
            if candidate_since <= today:
                since_day = candidate_since
        except (TypeError, ValueError):
            since_day = None

    if since_day is None:
        # First entry into this band, a flip from the other band, or a
        # corrupted/future anchor -- fresh clock, no drip yet.
        _write({"band": band, "since_day": today, "last_drip_day": None})
        return None

    if today - since_day < SUSTAINED_DRIP_DAYS_REQUIRED:
        return None  # not yet sustained -- tracker unchanged, no write

    last_drip_day: Optional[int] = None
    try:
        last_drip_raw = tracker.get("last_drip_day")
        last_drip_day = int(last_drip_raw) if last_drip_raw is not None else None
    except (TypeError, ValueError):
        last_drip_day = None

    if last_drip_day is not None and last_drip_day >= today:
        return None  # already dripped this canonical day -- idempotent no-op

    if band == "heroic":
        apply_faction_rep_delta(
            db, player.id, FactionType.OUTLAWS, SUSTAINED_HEROIC_DRIP_DELTA,
            "Sustained Heroic+ personal reputation (7+ canonical days) -- "
            "Fringe Alliance distrust drip",
        )
    else:
        apply_faction_rep_delta(
            db, player.id, FactionType.MERCHANTS, SUSTAINED_OUTLAW_DRIP_DELTA,
            "Sustained Outlaw+ personal reputation (7+ canonical days) -- "
            "Mercantile Guild distrust drip",
        )

    _write({"band": band, "since_day": since_day, "last_drip_day": today})
    return band


def _run_sustained_reputation_drip_sweep_sync() -> Dict[str, int]:
    """Own-session wrapper that DRIVES ``apply_sustained_reputation_drip``
    for every candidate player, once per canonical day per player
    (WO-PROG-SUSTAINED-DRIPS).

    DISCIPLINE — mirrors _run_bounty_accrual_sweep_sync / _run_daily_
    stipend_sweep_sync EXACTLY: own SessionLocal (never the request session,
    never the async engine); xact-level advisory lock so a second
    gameserver instance skips instead of double-dripping (the lock
    auto-releases on the first commit), then commit immediately to claim the
    sweep without pinning the lock; a candidate-id query, then a per-row
    with_for_update re-read so a concurrent reputation change can't race the
    drip; per-row commit and per-row try/except — one bad player cannot
    abort the batch or roll back an already-processed one.

    CANDIDATE GATE: a player is a candidate iff EITHER (a) currently in a
    sustained band (personal_reputation >= +250 or <= -250 — so a fresh
    entry can start its clock, and an existing sustained player can keep
    dripping), OR (b) already carries a ``sustained_tier`` settings tracker
    (via the JSONB ``?`` has_key operator) even if no longer in-band — this
    second arm is what lets a player who has DROPPED OUT of the sustained
    range (e.g. Heroic -> Lawful) get their tracker CLEARED by apply_
    sustained_reputation_drip on the next sweep wake; without it, a player
    who fell out of range would never be re-selected and their stale
    since_day would incorrectly resume counting (false partial credit) if
    they climbed back into the same band later. The per-row re-read + the
    state machine inside apply_sustained_reputation_drip re-confirm
    everything on the locked row regardless of which arm matched.

    Returns {"players_scanned", "heroic_dripped", "outlaw_dripped"} —
    the drip counts are NONZERO-drip counts (a scanned player who didn't
    drip this call — not yet sustained, already dripped today, or a
    clear/reset with no drip — is not counted in either)."""
    from src.core.database import SessionLocal

    result = {"players_scanned": 0, "heroic_dripped": 0, "outlaw_dripped": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _SUSTAINED_DRIP_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as every other daily sweep): claim the sweep, then iterate.
        db.commit()

        today = canonical_day_number()

        candidate_ids = (
            db.query(Player.id)
            .filter(
                Player.is_active.is_(True),
                (
                    (Player.personal_reputation >= SUSTAINED_HEROIC_THRESHOLD)
                    | (Player.personal_reputation <= SUSTAINED_OUTLAW_THRESHOLD)
                    | (Player.settings.has_key(_SUSTAINED_TIER_SETTINGS_KEY))
                ),
            )
            .all()
        )

        for (player_id,) in candidate_ids:
            try:
                player = (
                    db.query(Player)
                    .filter(Player.id == player_id)
                    .with_for_update()
                    .first()
                )
                if player is None or not player.is_active:
                    db.rollback()  # release row lock; nothing to do
                    continue

                result["players_scanned"] += 1
                dripped = apply_sustained_reputation_drip(db, player, today)

                db.commit()  # drip/tracker-write + anchor advance commit atomically
                if dripped == "heroic":
                    result["heroic_dripped"] += 1
                elif dripped == "outlaw":
                    result["outlaw_dripped"] += 1
            except Exception:
                logger.exception(
                    "Sustained-reputation-drip sweep: processing failed for "
                    "player %s", player_id,
                )
                db.rollback()

        if result["heroic_dripped"] or result["outlaw_dripped"]:
            logger.info(
                "Sustained-reputation-drip sweep: canonical day %d -- "
                "%d Heroic+ drip(s) (Fringe Alliance), %d Outlaw+ drip(s) "
                "(Mercantile Guild), %d candidate(s) scanned",
                today, result["heroic_dripped"], result["outlaw_dripped"],
                result["players_scanned"],
            )
        return result
    except Exception:
        logger.exception("Sustained-reputation-drip sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Team-reputation recalculation sweep (WO-RT-TEAM-REP held wiring)
# ---------------------------------------------------------------------------

def _run_team_reputation_sweep_sync() -> Dict[str, int]:
    """Recalculate every team whose TeamReputation.next_recalculation is
    due. Uses team_reputation_service's own pre-declared
    TEAM_REPUTATION_SWEEP_LOCK_KEY ('TREP') rather than a locally-derived
    one — GCRB/PRSW precedent applied to a lock key owned by its SOURCE
    module and imported here, not redeclared. Returns {due, recalculated}."""
    from src.core.database import SessionLocal
    from src.services.team_reputation_service import (
        TEAM_REPUTATION_SWEEP_LOCK_KEY,
        sweep_due_team_reputations,
    )

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": TEAM_REPUTATION_SWEEP_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return {"due": 0, "recalculated": 0}
        if not _sweep_due_and_advance(
            db, _TEAM_REPUTATION_SWEEP_STATE_KEY, TEAM_REPUTATION_SWEEP_SECONDS, datetime.now(UTC),
        ):
            return {"due": 0, "recalculated": 0}
        result = sweep_due_team_reputations(db)
        db.commit()
        return result
    except Exception:
        logger.exception("Team-reputation sweep failed")
        db.rollback()
        return {"due": 0, "recalculated": 0}
    finally:
        db.close()

