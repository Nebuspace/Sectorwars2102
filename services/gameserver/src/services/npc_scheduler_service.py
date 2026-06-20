"""NPC Scheduler Service — Loops A/B/C (SYSTEMS/npc-scheduler.md).

The first true game-economy background worker in this codebase. Hosted as
ONE asyncio task created in main.py's lifespan (gated by
``NPC_SCHEDULER_ENABLED``); the task wakes every 60s and dispatches the
loops whose cadence is due:

  Loop A —  5 min — schedule executor: resolve each NPC's current
            daily_schedule block, transition current_activity, and move
            patrolling NPCs along their routes (one hop per eligibility
            window, canon ~86s/turn pacing in npc_movement_service).
  Loop B — 10 min — roster maintenance: count live NPCs per NPCRoster,
            spawn replacements toward target_count (ADR-0063: vacancies
            fill IMMEDIATELY with a reduced-stat RECRUIT — the 7-day
            recruit window is a lifecycle STAGE, never a vacancy delay),
            and promote recruits whose stage has elapsed.
  Loop C — 30 min — off-duty rotation. FRAME ONLY in this phase: canon
            rotation (~20% off-duty resting 4-8h) needs the lodging
            tables (NPCBarracks/OutlawBase) to give off-duty NPCs
            somewhere to BE — deferred with the lodging slice; the hook
            stays a graceful no-op.

ASYNC/SYNC BRIDGE: tick bodies are sync (SessionLocal + the sync ORM the
rest of the game logic uses) and run via ``asyncio.to_thread`` so the
uvicorn event loop is never blocked by row-lock waits. Each tick body
commits, closes its session, and RETURNS a list of realtime event dicts;
the async wrapper then broadcasts them via connection_manager — never
from the worker thread.

SCHEDULE CLOCK: daily schedules are CANONICAL 24h days. The canonical
minute-of-day is derived from epoch-seconds × GAME_TIME_SCALE, so at
scale 1.0 it matches the real UTC clock and on dev (scale 144) a
canonical day elapses every 10 wall-clock minutes.

Multi-instance guard: each tick takes a Postgres session advisory lock;
a second gameserver instance running the scheduler skips its tick
instead of double-driving NPCs (canon: per-region advisory lock — a
single global lock is the degenerate single-instance form).

Patrol-route block shape (carried in daily_schedule.blocks[], canon
location_type ``patrol_route`` with the route inlined as location_ref —
canon's separate patrol-route registry is Design-only):

    {"start_minute": 510, "end_minute": 990, "activity": "patrol",
     "location_type": "patrol_route",
     "location_ref": {"sectors": [12, 13, 14], "minutes_per_sector": 240}}
"""

import asyncio
import logging
import random
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.npc_character import (
    NPCArchetype,
    NPCCharacter,
    NPCActivity,
    NPCLifecycleStage,
    NPCRoster,
    NPCStatus,
)
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import ShipSpecification
from src.services import npc_movement_service
from src.services.npc_spawn_service import (
    KIND_CONFIG,
    POLICE_WANTED_THRESHOLD,
    TRADER_SHIP_NOUN,
    TRADER_SHIP_TYPES,
    TRADER_STARTING_CREDITS,
    TRADER_TITLES,
    TRADER_TITLES_BY_TIER,
    notoriety_from_title,
    notoriety_tier,
    roll_notoriety,
    _build_npc_ship,
    _presence_entry,
    _roman,
)

logger = logging.getLogger(__name__)

# Wake interval of the host task; loop cadences must be multiples.
TICK_SECONDS = 60
# ADR-0042: the PendingEngagement sweep runs every minute, distinct
# from Loop A.
ENGAGEMENT_SWEEP_SECONDS = 60
# Loop A runs every tick (60s). The OLD 5-minute cadence made patrols read as
# dead: a co-located squad held position for 4m59s then teleported one hop in
# unison. At the tick cadence, combined with the per-NPC phase stagger in
# _drive_patrol, only a slice of any squad hops each minute — continuous motion
# instead of a herd pulse, and a clumped squad smears across its route.
LOOP_A_SECONDS = 60
LOOP_B_SECONDS = 10 * 60
LOOP_C_SECONDS = 30 * 60
# Genesis formation-completion sweep cadence. The 48h formation timer is
# coarse, so a 5-minute sweep settles a finished planet promptly without
# churning the DB. Process-relative is fine here: a missed boundary on restart
# just defers completion to the next sweep (the formation_complete_at timestamp
# stays authoritative — nothing is lost), unlike the weekly decay which needs a
# durable anchor.
GENESIS_COMPLETION_SECONDS = 5 * 60
# Planetary lazy-advance sweep cadence (terraforming progress + siege turns).
# Both systems were written as advance-on-READ only — TerraformingService.
# _advance_terraforming / PlanetaryService.advance_siege apply every tick
# accrued since the last anchor, but NOTHING drove them, so a project (or a
# besieged colony) whose owner never re-opened its planet screen simply
# stalled. This sweep makes the canonical clock authoritative for ALL such
# planets, not just those a player happens to read. Both advance methods are
# time-accurate (apply exactly the ticks elapsed) and idempotent (a no-op once
# caught up), so a 5-minute sweep is finer than the smallest tick period
# (terraforming periods are canonical-hours; one siege turn = 24 canonical
# hours) — progress never visibly lags, and process-relative cadence is fine
# because the durable per-planet anchor (last_tick_at / siege_turns +
# siege_started_at) stays authoritative across restarts. Same shape as the
# genesis completion sweep above.
PLANETARY_ADVANCE_SECONDS = 5 * 60

# Regional governance sweep cadence (open due elections, close+tally elections
# past their window, finalize policies past their window). The state machine is
# driven by wall-clock voting windows (voting_opens_at / voting_closes_at /
# RegionalPolicy.voting_closes_at are absolute timestamps set at creation), so
# this sweep — like the genesis/planetary sweeps — keys off the per-row durable
# timestamp, not a process-relative clock: a missed boundary on restart is just
# processed by the next sweep (nothing is lost). A 5-minute cadence settles a
# closed election/policy promptly without churning the DB; idempotent + a clean
# no-op when nothing is due.
GOVERNANCE_SWEEP_SECONDS = 5 * 60

# Weekly maintenance (reputation/relationship decay). Unlike Loops A/B/C, a
# weekly job CANNOT key off the process-relative ``elapsed_seconds`` clock —
# that counter resets on every restart, so an ``elapsed % week == 0`` guard
# would skip the week whenever the process bounced (and could double-fire if it
# bounced twice in a week). Instead a CHEAP coarse elapsed pre-filter
# (WEEKLY_DECAY_CHECK_SECONDS) decides when to even LOOK, and the real
# once-per-week guarantee comes from a DURABLE anchor: the canonical-week index
# of the last completed run, persisted in ``Galaxy.state`` (see
# _run_weekly_decay_sync). The cadence is measured in CANONICAL weeks so it is
# observable on dev (GAME_TIME_SCALE=144 → a canonical week elapses in ~70
# wall-clock minutes) and self-consistent with the rest of the scheduler's
# canonical clock. The whole job is FULLY SYNCHRONOUS (all three decays run on
# one work session in one advisory-locked transaction with the anchor advance —
# atomic) — no asyncio/AsyncSession, which would poison the shared async engine
# pool. The decay MAGNITUDES are wall-clock-semantic (faction's 30-day window,
# ARIA's per-day point) — that canonical-cadence / wall-clock-magnitude tension
# is intentional for dev observability and flagged in the run report.
CANONICAL_WEEK_DAYS = 7
# Galaxy.state JSONB key holding the canonical-week index of the last completed
# weekly-decay run (durable across restarts → no skipped/double weeks).
_WEEKLY_DECAY_STATE_KEY = "weekly_decay_last_week"
# Coarse CHEAP pre-filter cadence for the weekly-decay check. The durable
# canonical-week anchor is what actually guarantees once-per-week; this only
# keeps us from taking the advisory lock + querying Galaxy.state every 60s. A
# 15-minute pre-filter is far finer than a (canonical) week, so the week is
# never missed, while idle wakes do nothing.
WEEKLY_DECAY_CHECK_SECONDS = 15 * 60
# Coarse pre-filter for the economy faucet (reputation stipend + citizen
# perk).  Same rationale as WEEKLY_DECAY_CHECK_SECONDS: the durable
# canonical-week anchor inside run_weekly_faucet_sync is what actually
# guarantees once-per-week; this keeps us from acquiring the advisory lock +
# querying Galaxy.state on every 60s tick.  Intentionally offset from the
# decay pre-filter (15 min) by 5 minutes to avoid both hitting Postgres in
# the same scheduler wake.
FAUCET_CHECK_SECONDS = 20 * 60

# Economy-metrics snapshot pre-filter. The EconomicMetrics table is READ by
# economy_analytics_service (admin economy dashboard "latest metrics" panel)
# but, before this sweep, NOTHING ever WROTE a row — so the dashboard showed
# 0/empty forever. This writes one daily snapshot of galaxy-wide economic state
# (total credits in circulation, market trade volume, active traders, credit
# velocity). Like the genesis/planetary/governance sweeps, the cadence is a
# COARSE elapsed pre-filter (so we don't take the advisory lock + probe the DB
# every 60s); the once-per-day guarantee comes from a DURABLE anchor — the
# unique, midnight-truncated EconomicMetrics.date column (a same-day row already
# present → the sweep is a clean no-op). A 1-hour pre-filter is far finer than a
# day, so the day's snapshot is never missed even across restarts (the
# process-relative elapsed counter resets, but the durable date row does not).
# Offset 25 minutes from the faucet/decay pre-filters so the three coarse probes
# don't all hit Postgres on the same scheduler wake.
ECONOMY_SNAPSHOT_CHECK_SECONDS = 25 * 60

# Session-level advisory lock key (pg_try_advisory_xact_lock argument).
_ADVISORY_LOCK_KEY = 0x53573231  # 'SW21'

# ADR-0063: recruit lifecycle stage lasts 7 canonical days, then ACTIVE.
RECRUIT_STAGE_HOURS = 7 * 24

# ADR-0063 N-V4 genocide rapid-recovery. Detection and response windows
# are WALL-CLOCK (the trigger keys off real player behavior — a
# canonical window at dev time-scale would be seconds wide and
# undetectable); the halved recruit stage stays canonical. This
# interpretation is flagged for the docs repo.
GENOCIDE_KILL_THRESHOLD = 3
GENOCIDE_WINDOW_MINUTES = 30
GENOCIDE_RESPONSE_MINUTES = 60

# Statuses that count toward a roster's live headcount (DATA_MODELS/
# npcs.md Loop B query; ENGAGED_PENDING_ARRIVAL counts as committed,
# RESPAWNING slots are vacant).
_LIVE_STATUSES = (
    NPCStatus.ON_DUTY,
    NPCStatus.OFF_DUTY,
    NPCStatus.ENGAGED,
    NPCStatus.ENGAGED_PENDING_ARRIVAL,
)

# Loop A only drives NPCs in these statuses.
_SCHEDULABLE_STATUSES = (NPCStatus.ON_DUTY, NPCStatus.OFF_DUTY)


# ---------------------------------------------------------------------------
# Canonical schedule clock
# ---------------------------------------------------------------------------

def canonical_minute_of_day(now: Optional[datetime] = None) -> int:
    """Canonical minute-of-day [0, 1440). At GAME_TIME_SCALE=1.0 this is
    the real UTC minute-of-day; on dev the canonical day spins faster."""
    now = now or datetime.now(UTC)
    canonical_minutes = now.timestamp() * game_time.GAME_TIME_SCALE / 60.0
    return int(canonical_minutes % 1440)


def canonical_day_number(now: Optional[datetime] = None) -> int:
    """Canonical day counter since epoch (drives multi-day route
    cycles)."""
    now = now or datetime.now(UTC)
    return int(now.timestamp() * game_time.GAME_TIME_SCALE // 86400)


def canonical_weekday(now: Optional[datetime] = None) -> int:
    """Canonical weekday, Monday=0 (matches datetime.weekday() at scale
    1.0 — 1970-01-01 was a Thursday)."""
    return (canonical_day_number(now) + 3) % 7


def canonical_week_number(now: Optional[datetime] = None) -> int:
    """Monotonic canonical-week index since epoch — the durable cadence anchor
    for the weekly decay job. Increments once per canonical week regardless of
    process restarts, so persisting the last value gates the job exactly once
    per week."""
    return canonical_day_number(now) // CANONICAL_WEEK_DAYS


def resolve_schedule_block(
    daily_schedule: Dict[str, Any],
    minute: int,
    weekday: int,
    day_number: int = 0,
) -> Optional[Dict[str, Any]]:
    """The schedule block covering ``minute``, honoring weekly_overrides
    (SYSTEMS/npc-lifecycle.md JSONB shape) and multi-day route cycles
    (TRADER pattern: blocks come from days[day_number % cycle_days]).
    None when nothing matches."""
    if not daily_schedule:
        return None
    shift = int(daily_schedule.get("shift_offset_hours") or 0)
    minute = (minute + shift * 60) % 1440

    blocks = daily_schedule.get("blocks") or []
    route_cycle = daily_schedule.get("route_cycle")
    if isinstance(route_cycle, dict):
        cycle_days = max(1, int(route_cycle.get("cycle_days") or 1))
        blocks = (route_cycle.get("days") or {}).get(
            str(day_number % cycle_days)
        ) or blocks
    for override in daily_schedule.get("weekly_overrides") or []:
        if override.get("weekday") == weekday and override.get("blocks"):
            blocks = override["blocks"]
            break

    for block in blocks:
        try:
            start = int(block.get("start_minute", -1))
            end = int(block.get("end_minute", -1))
        except (TypeError, ValueError):
            continue
        if start <= minute < end:
            return block
    return None


# ---------------------------------------------------------------------------
# Loop A — schedule executor
# ---------------------------------------------------------------------------

def run_loop_a(db: Session, tick: int = 0) -> List[Dict[str, Any]]:
    """Resolve schedule blocks, transition activities, move patrollers.

    ``tick`` is the monotonic Loop-A invocation index; it feeds the per-NPC
    patrol stagger in ``_drive_patrol`` so co-located squads disperse rather
    than advancing in unison.
    """
    events: List[Dict[str, Any]] = []
    now = datetime.now(UTC)
    minute = canonical_minute_of_day(now)
    weekday = canonical_weekday(now)
    day_number = canonical_day_number(now)

    npcs = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status.in_(_SCHEDULABLE_STATUSES),
            NPCCharacter.lifecycle_stage.notin_(
                (NPCLifecycleStage.KIA, NPCLifecycleStage.RETIRED)
            ),
        )
        .all()
    )

    # Assign an EVEN phase to every NPC sharing a patrol route. A phase hashed
    # from the id alone splits unevenly on a small squad (e.g. 14 NPCs -> 9/3/2
    # across 3 sectors), which clumps them into a subset of waypoints and lets a
    # sector — notably the capital — fall empty. Enumerating each route's squad
    # (stable id sort) and assigning index % N spreads them uniformly, so ~1/N
    # is anchored to each waypoint. Phase seeds both the cursor and the per-tick
    # stagger in _drive_patrol.
    from collections import defaultdict
    route_squads: Dict[tuple, List[NPCCharacter]] = defaultdict(list)
    for npc in npcs:
        b = resolve_schedule_block(
            npc.daily_schedule or {}, minute, weekday, day_number
        )
        if b is None or str(b.get("location_type")) != "patrol_route":
            continue
        ref = b.get("location_ref")
        if isinstance(ref, dict):
            secs = tuple(int(s) for s in (ref.get("sectors") or []))
        elif isinstance(ref, list):
            secs = tuple(int(s) for s in ref)
        else:
            secs = ()
        if secs:
            route_squads[secs].append(npc)
    patrol_phase: Dict[Any, int] = {}
    for secs, squad in route_squads.items():
        squad.sort(key=lambda member: str(member.id))
        width = len(secs)
        for i, member in enumerate(squad):
            patrol_phase[member.id] = i % width

    for npc in npcs:
        block = resolve_schedule_block(
            npc.daily_schedule or {}, minute, weekday, day_number
        )
        if block is None:
            continue

        # Activity transition (graceful on unknown vocabulary).
        activity_name = str(block.get("activity", "")).upper()
        try:
            activity = NPCActivity[activity_name]
        except KeyError:
            continue
        if npc.current_activity != activity:
            npc.current_activity = activity

        location_type = str(block.get("location_type", ""))

        # Movement/trade drivers. Location types without a driver yet
        # (home_sector, lodging) no-op gracefully until their slices land.
        try:
            if (
                activity == NPCActivity.PATROL
                and npc.status == NPCStatus.ON_DUTY
                and location_type == "patrol_route"
            ):
                events.extend(
                    _drive_patrol(
                        db, npc, block, minute, tick,
                        patrol_phase.get(npc.id, 0),
                    )
                )
            elif (
                activity == NPCActivity.COMMUTE
                and location_type == "station_target"
            ):
                events.extend(_drive_commute(db, npc, block))
            elif (
                activity == NPCActivity.WORK_STATION
                and location_type == "station"
            ):
                events.extend(_drive_trade_stop(db, npc, block))
            elif (
                activity == NPCActivity.WORK_STATION
                and location_type == "mission_stop"
            ):
                events.extend(_drive_mission_stop(db, npc, block))
        except Exception:
            logger.exception("Loop A: drive failed for NPC %s", npc.id)
            db.rollback()

    db.flush()
    return events


def _drive_patrol(
    db: Session,
    npc: NPCCharacter,
    block: Dict[str, Any],
    minute: int,
    tick: int = 0,
    phase: int = 0,
) -> List[Dict[str, Any]]:
    """Advance a patrolling NPC one hop along its route.

    The NPC follows its route as a cycle via a per-NPC cursor persisted in the
    schedule block (``patrol_cursor``): it heads for ``sectors[cursor]`` and,
    on arrival, advances the cursor to the next waypoint. Targeting a waypoint
    cursor — rather than the sector AFTER the NPC's current position, as the
    earlier round-robin did — is what makes STAR-shaped routes traverse
    correctly: when the host sits between two non-adjacent neighbours, a
    position-derived target oscillates host<->neighbour and the third sector is
    never visited; a cursor that only advances on arrival walks the full cycle.

    ``phase`` is assigned EVENLY per route by the caller (``run_loop_a``), not
    hashed from the id — a hash distributes unevenly on a small squad and lets
    a sector (notably the capital) fall empty. It seeds the cursor so the squad
    starts dispersed across all waypoints, and gates which slice of the squad
    hops this tick so the squad churns through a sector a few at a time rather
    than teleporting as a block. Net effect: ~1/N of every squad is anchored to
    each waypoint at all times, so no sector on the route is ever empty.
    """
    ref = block.get("location_ref")
    if isinstance(ref, dict):
        sectors = [int(s) for s in (ref.get("sectors") or [])]
    elif isinstance(ref, list):
        sectors = [int(s) for s in ref]
    else:
        return []
    n = len(sectors)
    if n < 2 or npc.current_sector_id is None:
        return []

    # Stagger: only the phase-matching slice of the squad hops this tick, so a
    # route's NPCs arrive and depart a sector a few at a time (continuous churn)
    # rather than the whole squad moving in unison.
    if (tick + phase) % n != 0:
        return []

    # Per-NPC route cursor — defaults to the even phase until first persisted,
    # so an un-migrated NPC still starts at a dispersed, route-correct slot.
    try:
        cursor = int(block.get("patrol_cursor", phase)) % n
    except (TypeError, ValueError):
        cursor = phase % n
    desired = sectors[cursor]
    if npc.current_sector_id == desired:
        # Arrived at the current waypoint — advance the cursor and aim for the
        # next, so the NPC keeps moving instead of idling on top of its target.
        cursor = (cursor + 1) % n
        block["patrol_cursor"] = cursor
        flag_modified(npc, "daily_schedule")
        desired = sectors[cursor]

    next_hop = npc_movement_service.next_hop_toward(
        db, npc.current_sector_id, desired
    )
    if next_hop is None:
        return []
    return npc_movement_service.move_npc(db, npc, next_hop)


def _drive_commute(
    db: Session,
    npc: NPCCharacter,
    block: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Move a commuting NPC (trader transit day) one hop toward its
    target sector; npc_movement_service enforces canon pacing."""
    ref = block.get("location_ref") or {}
    target = ref.get("sector_id")
    if target is None or npc.current_sector_id is None:
        return []
    target = int(target)
    if npc.current_sector_id == target:
        return []
    next_hop = npc_movement_service.next_hop_toward(
        db, npc.current_sector_id, target
    )
    if next_hop is None:
        return []
    return npc_movement_service.move_npc(db, npc, next_hop)


def _drive_trade_stop(
    db: Session,
    npc: NPCCharacter,
    block: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Run a trader's work_station block: execute the stop's sell/buy
    program once the NPC is at the station's sector (a trader that fell
    behind keeps commuting instead)."""
    ref = block.get("location_ref") or {}
    if not ref.get("station_id"):
        return []
    if npc.current_sector_id != ref.get("sector_id"):
        # Behind schedule — keep flying toward the stop.
        return _drive_commute(db, npc, block)

    route = (npc.daily_schedule or {}).get("trade_route") or []
    stop_index = ref.get("stop_index")
    stop = None
    if stop_index is not None and 0 <= int(stop_index) < len(route):
        stop = route[int(stop_index)]
    if stop is None:
        stop = {"station_id": ref["station_id"],
                "sector_id": ref.get("sector_id"), "buy_here": []}

    from src.services import npc_trading_service
    return npc_trading_service.run_trade_stop(db, npc, stop)


def _drive_mission_stop(
    db: Session,
    npc: NPCCharacter,
    block: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Drive a colonist-courier / science-vessel mission stop: fly to the
    stop's sector, then execute its action (load colonists / deliver them and
    grow the planet / survey). A courier that fell behind keeps commuting."""
    ref = block.get("location_ref") or {}
    target_sector = ref.get("sector_id")
    if target_sector is None:
        return []
    if npc.current_sector_id != target_sector:
        # Behind schedule — keep flying toward the stop.
        return _drive_commute(db, npc, block)

    from src.services import npc_mission_service
    route = (npc.daily_schedule or {}).get("mission_route") or []
    stop_index = ref.get("stop_index")
    stop = None
    if stop_index is not None and 0 <= int(stop_index) < len(route):
        stop = route[int(stop_index)]
    if stop is None:
        stop = {"sector_id": ref.get("sector_id"),
                "planet_id": ref.get("planet_id"),
                "action": ref.get("action")}
    return npc_mission_service.run_mission_stop(db, npc, stop)


# ---------------------------------------------------------------------------
# Loop B — roster maintenance
# ---------------------------------------------------------------------------

def run_loop_b(db: Session) -> List[Dict[str, Any]]:
    """Roster maintenance: resurrect cooled-down respawners, fill
    deficits (immediate recruit fill, ADR-0063), promote recruits whose
    stage elapsed, and apply the genocide rapid-recovery flood."""
    events: List[Dict[str, Any]] = []
    now = datetime.now(UTC)

    # ADR-0063 N-D2: respawn-permitted NPCs return as the SAME identity
    # at full stats once the 15-minute cooldown elapses.
    try:
        events.extend(_resurrect_respawned(db, now))
    except Exception:
        logger.exception("Loop B: respawn resurrection failed")
        db.rollback()

    # Recruit → active promotion. promotion_pending_at is the explicit
    # deadline when set (genocide-flood recruits run half-stage);
    # otherwise the canonical 7-day stage from spawn.
    recruits = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.lifecycle_stage == NPCLifecycleStage.RECRUIT,
            NPCCharacter.status.in_(_LIVE_STATUSES),
        )
        .all()
    )
    for npc in recruits:
        if npc.promotion_pending_at is not None:
            if now >= npc.promotion_pending_at:
                npc.lifecycle_stage = NPCLifecycleStage.ACTIVE
        elif (
            npc.spawned_at
            and game_time.canonical_hours_since(npc.spawned_at) >= RECRUIT_STAGE_HOURS
        ):
            npc.lifecycle_stage = NPCLifecycleStage.ACTIVE

    flooded_regions = _genocide_flood_regions(db, now)

    for roster in db.query(NPCRoster).all():
        try:
            spawned = _fill_roster_deficit(
                db, roster,
                rapid_recovery=roster.region_id in flooded_regions,
            )
            events.extend(spawned)
        except Exception:
            logger.exception("Loop B: roster fill failed for %s", roster.id)
            db.rollback()

    db.flush()
    return events


def _genocide_flood_regions(db: Session, now: datetime) -> set:
    """Region ids currently under the N-V4 rapid-recovery flood: ≥3
    law-enforcement KIAs inside a 30-minute window, response active for
    1 hour after the triggering kill."""
    from src.models.npc_character import NPCArchetype, NPCDeathLog

    lookback = timedelta(
        minutes=GENOCIDE_WINDOW_MINUTES + GENOCIDE_RESPONSE_MINUTES
    )
    rows = (
        db.query(NPCDeathLog.home_region_id, NPCDeathLog.killed_at)
        .join(NPCCharacter, NPCDeathLog.npc_id == NPCCharacter.id)
        .filter(
            NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
            NPCDeathLog.killed_at >= now - lookback,
            NPCDeathLog.home_region_id.isnot(None),
        )
        .order_by(NPCDeathLog.home_region_id, NPCDeathLog.killed_at)
        .all()
    )

    by_region: Dict[Any, List[datetime]] = {}
    for region_id, killed_at in rows:
        if killed_at is not None and killed_at.tzinfo is None:
            killed_at = killed_at.replace(tzinfo=UTC)
        by_region.setdefault(region_id, []).append(killed_at)

    flooded = set()
    window = timedelta(minutes=GENOCIDE_WINDOW_MINUTES)
    response = timedelta(minutes=GENOCIDE_RESPONSE_MINUTES)
    for region_id, kills in by_region.items():
        for i in range(len(kills) - GENOCIDE_KILL_THRESHOLD + 1):
            third = kills[i + GENOCIDE_KILL_THRESHOLD - 1]
            if third - kills[i] <= window and now - third <= response:
                flooded.add(region_id)
                logger.warning(
                    "npc.coordinated_genocide_detected: region %s — "
                    "rapid-recovery flood active (2x recruiting, "
                    "half-stage recruits)",
                    region_id,
                )
                break
    return flooded


def _resurrect_respawned(db: Session, now: datetime) -> List[Dict[str, Any]]:
    """Bring cooled-down RESPAWNING NPCs back into their slot (same
    identity, full stats, fresh hull at the roster's host sector)."""
    events: List[Dict[str, Any]] = []
    due = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status == NPCStatus.RESPAWNING,
            NPCCharacter.respawn_eligible_at.isnot(None),
            NPCCharacter.respawn_eligible_at <= now,
        )
        .all()
    )
    for npc in due:
        roster = (
            db.query(NPCRoster)
            .filter(NPCRoster.bang_roster_ref == npc.bang_roster_ref)
            .first()
        )
        cfg = KIND_CONFIG.get(roster.role) if roster is not None else None
        if roster is None or cfg is None:
            continue
        spec = (
            db.query(ShipSpecification)
            .filter(ShipSpecification.type == cfg.ship_type)
            .first()
        )
        if spec is None:
            continue
        sector = (
            db.query(Sector)
            .filter(Sector.sector_id == roster.host_sector_id)
            .with_for_update()
            .first()
        )
        if sector is None:
            continue

        ship = _build_npc_ship(
            spec,
            name=cfg.ship_name_format.format(name=npc.name),
            sector_id=roster.host_sector_id,
        )
        db.add(ship)
        db.flush()

        npc.ship_id = ship.id
        npc.status = NPCStatus.ON_DUTY
        npc.current_sector_id = roster.host_sector_id
        npc.respawn_eligible_at = None
        npc.last_seen_at = now

        npc_movement_service.add_npc_presence(sector, npc, ship)
        if cfg.joins_squad:
            _join_squad(sector, cfg, roster, npc)

        logger.info(
            "Loop B: %s respawned after cooldown (roster %s)",
            npc.display_name, roster.bang_roster_ref,
        )
        events.append({
            "type": "npc_respawned",
            "sector_id": roster.host_sector_id,
            "npc_id": str(npc.id),
            "display_name": npc.display_name,
            "ship_id": str(ship.id),
            "ship_name": ship.name,
            "ship_type": ship.type.name,
            "is_npc": True,
            "timestamp": now.isoformat(),
        })
    return events


def _fill_roster_deficit(
    db: Session,
    roster: NPCRoster,
    rapid_recovery: bool = False,
    fill_all: bool = False,
) -> List[Dict[str, Any]]:
    """Spawn replacements when the roster is under target. Canon Loop B
    spawns one per pass (a wiped squad refills over successive passes, not
    instantaneously); the N-V4 genocide flood doubles the rate. ``fill_all``
    bypasses the cadence and fills the entire deficit in one pass — used by the
    startup bulk-fill so the galaxy reaches its full trader population promptly
    instead of one-per-10min."""
    cfg = KIND_CONFIG.get(roster.role)
    if cfg is None:
        # Roles without a spawn recipe yet (later slices) are tolerated.
        return []

    # RESPAWNING slots are reserved for the returning identity (ADR-0063
    # N-D2) — counting them prevents a recruit double-fill.
    occupied = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.bang_roster_ref == roster.bang_roster_ref,
            NPCCharacter.status.in_(_LIVE_STATUSES + (NPCStatus.RESPAWNING,)),
        )
        .count()
    )
    if occupied >= roster.target_count:
        return []

    spec = (
        db.query(ShipSpecification)
        .filter(ShipSpecification.type == cfg.ship_type)
        .first()
    )
    if spec is None:
        logger.warning(
            "Loop B: no ShipSpecification for %s — cannot fill roster %s",
            cfg.ship_type.name, roster.id,
        )
        return []

    # Sector lock before the presence/squad JSONB read-modify-write.
    sector = (
        db.query(Sector)
        .filter(Sector.sector_id == roster.host_sector_id)
        .with_for_update()
        .first()
    )
    if sector is None:
        logger.warning(
            "Loop B: roster %s host sector %s not found",
            roster.id, roster.host_sector_id,
        )
        return []

    now = datetime.now(UTC)
    deficit = roster.target_count - occupied
    if fill_all:
        spawn_count = deficit
    else:
        spawn_count = min(deficit, 2 if rapid_recovery else 1)
    stage_hours = RECRUIT_STAGE_HOURS / 2 if rapid_recovery else RECRUIT_STAGE_HOURS

    has_primary = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.bang_roster_ref == roster.bang_roster_ref,
            NPCCharacter.status.in_(_LIVE_STATUSES),
            NPCCharacter.duty_role.like("primary%"),
        )
        .count()
        > 0
    )

    is_trader = roster.default_archetype == NPCArchetype.TRADER
    # Per-hull spec cache so trader variety doesn't re-query the same spec.
    spec_cache: Dict[Any, ShipSpecification] = {}

    # Trader route pool: generate up to 8 randomized routes ONCE and assign
    # spawns from it. generate_trade_route runs an expensive warp-graph BFS, so
    # generating one per spawn would make a bulk fill crawl AND hold the
    # scheduler advisory lock for minutes (freezing all NPC movement). A shared
    # pool keeps the fill fast while the randomized variants still give the
    # squad varied lanes. Empty pool → the region has no complementary route, so
    # defer (retried next pass as markets move).
    route_pool: List[List[Dict[str, Any]]] = []
    colonist_pool: List[List[Dict[str, Any]]] = []
    science_pool: List[List[Dict[str, Any]]] = []
    if is_trader:
        from src.services import npc_trading_service
        from src.services import npc_mission_service

        for _ in range(min(spawn_count, 8)):
            generated = npc_trading_service.generate_trade_route(
                db, roster.region_id, roster.host_sector_id
            )
            if generated is not None:
                route_pool.append(generated)
        # Mission pools (colonist couriers + science vessels). A few routes
        # each, generated once — generate_*_route runs a warp-graph BFS.
        for _ in range(3):
            cr = npc_mission_service.generate_colonist_route(db, roster.host_sector_id)
            if cr is not None:
                colonist_pool.append(cr)
        for _ in range(3):
            sr = npc_mission_service.generate_science_route(db, roster.host_sector_id)
            if sr is not None:
                science_pool.append(sr)
        if not route_pool and not colonist_pool and not science_pool:
            logger.info(
                "Loop B: no trade/mission routes available in region %s — "
                "trader spawn deferred", roster.region_id,
            )
            return []

    events: List[Dict[str, Any]] = []
    for _ in range(spawn_count):
        npc_name = _next_name(db, roster)

        # Defaults: the kind's single hull, title and ship-name convention.
        # Traders override all three below for variety.
        daily_schedule: Dict[str, Any] = dict(roster.schedule_template or {})
        spawn_spec = spec
        spawn_title = cfg.title
        ship_name = cfg.ship_name_format.format(name=npc_name)
        spawn_notoriety = None  # traders only (set below)

        if is_trader:
            # Roll the captain's mission: most run commerce (station commodity
            # routes), some are colonist couriers (hub → frontier-planet runs
            # that grow population and carry lootable colonists), a few are
            # science vessels surveying uninhabited worlds. Fall back to whatever
            # pool actually has routes.
            roll = random.random()
            if roll < 0.40 and colonist_pool:
                route = random.choice(colonist_pool)
                daily_schedule = npc_mission_service.build_mission_schedule(
                    route, npc_mission_service.COLONIST_MISSION)
            elif roll < 0.55 and science_pool:
                route = random.choice(science_pool)
                daily_schedule = npc_mission_service.build_mission_schedule(
                    route, npc_mission_service.SCIENCE_MISSION)
            elif route_pool:
                route = random.choice(route_pool)
                daily_schedule = npc_trading_service.build_trader_schedule(route)
            elif colonist_pool:
                daily_schedule = npc_mission_service.build_mission_schedule(
                    random.choice(colonist_pool), npc_mission_service.COLONIST_MISSION)
            else:
                daily_schedule = npc_mission_service.build_mission_schedule(
                    random.choice(science_pool), npc_mission_service.SCIENCE_MISSION)

            # Variety: each captain flies a different hull, carries a persona
            # title, and runs on a staggered day clock — so the lanes read as a
            # diverse merchant class AND the galaxy always has awake traders (a
            # shared sleep window would otherwise park them all at once).
            hull = random.choice(TRADER_SHIP_TYPES)
            if hull not in spec_cache:
                spec_cache[hull] = (
                    db.query(ShipSpecification)
                    .filter(ShipSpecification.type == hull)
                    .first()
                )
            spawn_spec = spec_cache[hull] or spec
            # Notoriety drives the persona: most captains are reputable, a
            # minority unscrupulous — and the title hints at which (a "Smuggler"
            # is fair game; a "Merchant Prince" is an innocent).
            spawn_notoriety = roll_notoriety(random.random())
            tier_titles = TRADER_TITLES_BY_TIER.get(
                notoriety_tier(spawn_notoriety), TRADER_TITLES
            )
            spawn_title = random.choice(tier_titles)
            ship_name = (
                f"{spawn_title} {npc_name}'s "
                f"{TRADER_SHIP_NOUN.get(hull, 'Hauler')}"
            )
            daily_schedule["shift_offset_hours"] = random.randint(0, 23)

        ship = _build_npc_ship(
            spawn_spec,
            name=ship_name,
            sector_id=roster.host_sector_id,
        )
        db.add(ship)
        db.flush()

        npc = NPCCharacter(
            name=npc_name,
            title=spawn_title,
            faction_code=roster.faction_code,
            archetype=roster.default_archetype,
            status=NPCStatus.ON_DUTY,
            current_sector_id=roster.host_sector_id,
            ship_id=ship.id,
            bang_roster_ref=roster.bang_roster_ref,
            home_region_id=roster.region_id,
            current_activity=(
                NPCActivity.COMMUTE if is_trader else NPCActivity.PATROL
            ),
            # ADR-0063: successors spawn immediately as reduced-stat recruits.
            lifecycle_stage=NPCLifecycleStage.RECRUIT,
            # N-F1: a roster with no live primary gets one immediately
            # (the emergency-spawn fallthrough); otherwise the recruit
            # lands in a backup slot. Traders are independent — no chain.
            duty_role=None if is_trader else (
                f"backup_{roster.role}" if has_primary
                else f"primary_{roster.role}"
            ),
            daily_schedule=daily_schedule,
            engagement_eligible_at=now,
            promotion_pending_at=game_time.scaled_deadline(stage_hours, start=now),
            # Wallet seed funds the first cargo load (canon-silent
            # amount — flagged in DECISIONS.md).
            credits=TRADER_STARTING_CREDITS if is_trader else 0,
            notoriety=spawn_notoriety,
            spawned_at=now,
            last_seen_at=now,
        )
        db.add(npc)
        db.flush()
        has_primary = True

        npc_movement_service.add_npc_presence(sector, npc, ship)
        if cfg.joins_squad:
            _join_squad(sector, cfg, roster, npc)
        db.flush()

        occupied += 1
        logger.info(
            "Loop B: spawned recruit %s for roster %s (%d/%d live%s)",
            npc.display_name, roster.bang_roster_ref, occupied,
            roster.target_count,
            ", rapid-recovery" if rapid_recovery else "",
        )
        events.append({
            "type": "npc_spawned",
            "sector_id": roster.host_sector_id,
            "npc_id": str(npc.id),
            "display_name": npc.display_name,
            "ship_id": str(ship.id),
            "ship_name": ship.name,
            "ship_type": ship.type.name,
            "is_npc": True,
            "timestamp": now.isoformat(),
        })
    return events


def _next_name(db: Session, roster: NPCRoster) -> str:
    """First pool name not already used under this roster ref; roman
    generation suffix once the pool wraps (mirrors materialize_from_bang)."""
    pool = roster.name_pool or {}
    names = [str(n) for n in (pool.get("names") or [])]
    if not names:
        names = [f"{roster.role.replace('_', ' ').title()}"]
    used = {
        row[0]
        for row in db.query(NPCCharacter.name)
        .filter(NPCCharacter.bang_roster_ref == roster.bang_roster_ref)
        .all()
    }
    for name in names:
        if name not in used:
            return name
    generation = 2
    while True:
        for name in names:
            candidate = f"{name} {_roman(generation)}"
            if candidate not in used:
                return candidate
        generation += 1


def _join_squad(
    sector: Sector,
    cfg,
    roster: NPCRoster,
    npc: NPCCharacter,
) -> None:
    """Append the NPC to its roster's patrol squad row (creating the row
    if the squad was wiped and deleted). Caller holds the sector lock."""
    import uuid as _uuid

    defenses = dict(sector.defenses or {})
    squads = list(defenses.get(cfg.defenses_key) or [])
    target = next(
        (s for s in squads if s.get("squad_kind") == cfg.squad_kind
         and s.get("faction_code") == roster.faction_code),
        None,
    )
    if target is None:
        target = {
            "patrol_id": str(_uuid.uuid4()),
            "faction_code": roster.faction_code,
            "squad_kind": cfg.squad_kind,
            "npc_character_ids": [],
            "ship_count": 0,
            "deployed_at": datetime.now(UTC).isoformat(),
        }
        if cfg.is_police:
            target["wanted_threshold"] = POLICE_WANTED_THRESHOLD
            target["scheduled_clear_at"] = None
        squads.append(target)

    ids = list(target.get("npc_character_ids") or [])
    if str(npc.id) not in ids:
        ids.append(str(npc.id))
    target["npc_character_ids"] = ids
    target["ship_count"] = len(ids)

    defenses[cfg.defenses_key] = squads
    sector.defenses = defenses
    flag_modified(sector, "defenses")


# ---------------------------------------------------------------------------
# Loop C — off-duty rotation (frame only in this phase)
# ---------------------------------------------------------------------------

def run_loop_c(db: Session) -> List[Dict[str, Any]]:
    """Canon rotation (~20% off-duty, 4-8h rest) needs the lodging slice
    (NPCBarracks/OutlawBase) so off-duty NPCs have somewhere to be —
    graceful no-op until then. The presence reconciliation sweep rides
    this cadence instead."""
    reconcile_presence(db)
    return []


def reconcile_presence(db: Session) -> int:
    """Periodic insurance against players_present drift (lost JSONB
    updates accumulate monotonically without it — ghost or missing NPC
    contacts). Rebuilds each touched sector's NPC entries from
    npc_characters.current_sector_id and prunes player entries whose
    Player row has moved elsewhere. Returns the number of repaired
    sectors."""
    from src.models.player import Player
    from src.models.ship import Ship

    # Expected NPC presence, from the relational source of truth.
    live_npcs = (
        db.query(NPCCharacter, Ship)
        .outerjoin(Ship, NPCCharacter.ship_id == Ship.id)
        .filter(
            NPCCharacter.status.in_(_LIVE_STATUSES),
            NPCCharacter.current_sector_id.isnot(None),
        )
        .all()
    )
    expected_npcs: Dict[int, Dict[str, Any]] = {}
    for npc, ship in live_npcs:
        if ship is None or ship.is_destroyed:
            continue
        expected_npcs.setdefault(npc.current_sector_id, {})[str(npc.id)] = (npc, ship)

    # Players' actual locations (for pruning relocated entries).
    player_sector = {
        str(pid): sid
        for pid, sid in db.query(Player.id, Player.current_sector_id).all()
    }

    # Sectors worth inspecting: any with presence entries, plus any that
    # should have NPC entries.
    sector_ids = set(expected_npcs.keys())
    rows = db.execute(
        text(
            "SELECT sector_id FROM sectors "
            "WHERE jsonb_array_length(players_present) > 0"
        )
    ).fetchall()
    sector_ids.update(int(r[0]) for r in rows)

    repaired = 0
    for sid in sorted(sector_ids):
        sector = (
            db.query(Sector)
            .filter(Sector.sector_id == sid)
            .with_for_update()
            .first()
        )
        if sector is None:
            continue

        current = list(sector.players_present or [])
        expected_here = expected_npcs.get(sid, {})
        rebuilt: List[Dict[str, Any]] = []
        seen_npc_ids = set()

        for entry in current:
            pid = entry.get("player_id")
            if entry.get("is_npc"):
                if pid in expected_here:
                    rebuilt.append(entry)
                    seen_npc_ids.add(pid)
                # else: stale NPC entry (moved/KIA) — drop it.
            else:
                # Keep player entries unless the Player row is known to
                # be elsewhere (unknown ids are left alone).
                if player_sector.get(pid, sid) == sid:
                    rebuilt.append(entry)

        for npc_id, (npc, ship) in expected_here.items():
            if npc_id not in seen_npc_ids:
                rebuilt.append(_presence_entry(npc, ship))

        if rebuilt != current:
            sector.players_present = rebuilt
            flag_modified(sector, "players_present")
            repaired += 1

    if repaired:
        db.flush()
        logger.info("Presence reconciliation repaired %d sector(s)", repaired)
    return repaired


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
            {"key": _ADVISORY_LOCK_KEY},
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
# Genesis — scheduled formation completion
# ---------------------------------------------------------------------------

def _run_genesis_completion_sync() -> int:
    """Complete forming genesis planets whose timer has elapsed.

    Before this tick, formation completion settled ONLY lazily — GenesisService.
    complete_due_formations runs on a player's owned-planets fetch and is scoped
    to that one player. A colony whose owner never re-checks the Colonial
    Registry (or an abandoned/unowned forming planet) would therefore stay
    "forming" forever past its 48h timer. This periodic sweep makes the timer
    authoritative for everyone. Cheap (an indexed forming/past-due filter that
    returns nothing on a steady galaxy), idempotent, xact-advisory-lock-gated
    so a second instance skips instead of double-completing."""
    from src.core.database import SessionLocal
    from src.services.genesis_service import GenesisService

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        # GenesisService.complete_all_due_formations commits internally when it
        # completes any planet; that commit also releases this xact lock.
        completed = GenesisService(db).complete_all_due_formations()
        if not completed:
            db.commit()  # release the lock on the no-op path
        return completed
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Planetary lazy-advance sweep — terraforming progress + siege turns
# ---------------------------------------------------------------------------

def _run_planetary_advance_sync() -> Dict[str, int]:
    """Drive terraforming, siege AND commodity production forward for planets.

    Before this sweep, TerraformingService._advance_terraforming,
    PlanetaryService.advance_siege and PlanetaryService.realize_production
    (commodity accrual) only ever ran when a player happened to read the
    affected planet (advance-on-read) — a colony whose owner never re-opened
    its screen would freeze mid-terraform, sit at full morale under siege, or
    stop banking the fuel/organics/equipment its colonists produce. This
    periodic sweep makes the canonical clock authoritative for ALL such
    planets, mirroring the genesis-completion sweep above.

    All three underlying advance methods are time-accurate (they apply exactly
    the ticks/elapsed accrued since the durable per-planet anchor —
    terraforming_progress, siege_turns, and last_production + the
    active_events['production_carry'] fractional bank respectively) and
    idempotent (a caught-up planet is a no-op), so running them on a fixed
    cadence neither over- nor under-awards: a planet read by its owner in
    between is simply already current when the sweep arrives, and the sweep
    + an interleaved read accrue exactly elapsed × rate ONCE.

    Cheap on a steady galaxy: the indexed filters (terraforming_active /
    under_siege / owned-and-colonized) return nothing or no-op rows when no
    planet qualifies, so the sweep is a safe no-op there. xact-advisory-lock-
    gated so a second instance skips instead of double-advancing. Per-planet
    failure is isolated and rolled back so one bad planet cannot abort the
    rest of the sweep.

    Returns {terraforming, siege, production} — the count of planets that
    actually moved in each phase.
    """
    from src.core.database import SessionLocal
    from src.models.planet import Planet
    from src.services.planetary_service import PlanetaryService
    from src.services.terraforming_service import TerraformingService

    result = {"terraforming": 0, "siege": 0, "production": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # Terraforming progression. _advance_terraforming mutates the planet
        # and leaves the commit to the caller, so we commit per planet (one
        # bad planet rolls back only itself).
        terra = TerraformingService(db)
        terra_planets = (
            db.query(Planet.id)
            .filter(Planet.terraforming_active.is_(True))
            .all()
        )
        for (planet_id,) in terra_planets:
            try:
                planet = (
                    db.query(Planet)
                    .filter(Planet.id == planet_id)
                    .with_for_update()
                    .first()
                )
                if planet is None:
                    continue
                if terra._advance_terraforming(planet):
                    db.commit()
                    result["terraforming"] += 1
                else:
                    db.rollback()  # release the row lock; nothing changed
            except Exception:
                logger.exception(
                    "Planetary advance: terraforming failed for planet %s",
                    planet_id,
                )
                db.rollback()

        # Siege progression. advance_siege mutates the planet and leaves the
        # commit to the caller — same per-planet commit/rollback discipline.
        planetary = PlanetaryService(db)
        siege_planets = (
            db.query(Planet.id)
            .filter(
                Planet.under_siege.is_(True),
                Planet.siege_started_at.isnot(None),
            )
            .all()
        )
        for (planet_id,) in siege_planets:
            try:
                planet = (
                    db.query(Planet)
                    .filter(Planet.id == planet_id)
                    .with_for_update()
                    .first()
                )
                if planet is None:
                    continue
                if planetary.advance_siege(planet):
                    db.commit()
                    result["siege"] += 1
                else:
                    db.rollback()  # release the row lock; nothing changed
            except Exception:
                logger.exception(
                    "Planetary advance: siege failed for planet %s", planet_id,
                )
                db.rollback()

        # Commodity production progression. realize_production accrues exactly
        # the fuel/organics/equipment (and research points) produced since the
        # durable last_production anchor and leaves the commit to the caller —
        # same per-planet commit/rollback discipline as terraforming/siege. The
        # filter targets owned, colonized planets only (owner_id set AND
        # colonists > 0); an unowned or empty planet produces nothing and is
        # skipped without a row lock. Idempotent: a player read between sweeps
        # leaves the planet already current, so the sweep is a clean no-op.
        production_planets = (
            db.query(Planet.id)
            .filter(
                Planet.owner_id.isnot(None),
                Planet.colonists > 0,
            )
            .all()
        )
        for (planet_id,) in production_planets:
            try:
                planet = (
                    db.query(Planet)
                    .filter(Planet.id == planet_id)
                    .with_for_update()
                    .first()
                )
                if planet is None:
                    continue
                if planetary.realize_production(planet):
                    db.commit()
                    result["production"] += 1
                else:
                    db.rollback()  # release the row lock; nothing changed
            except Exception:
                logger.exception(
                    "Planetary advance: production failed for planet %s",
                    planet_id,
                )
                db.rollback()

        # Release the advisory lock held on this session's transaction. Each
        # per-planet commit above already released it once; a final commit
        # closes out any open transaction (e.g. the rollback after the last
        # no-op planet) so the lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Planetary advance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Regional governance sweep — open/close elections + finalize policies
# ---------------------------------------------------------------------------

def _run_governance_sweep_sync() -> Dict[str, int]:
    """Drive the regional democratic loop forward on the canonical clock.

    Three idempotent phases mirroring the planetary advance sweep's discipline
    (own session, xact advisory lock, per-row with_for_update + per-row commit,
    per-row failure isolation):

      1. OPEN due elections: PENDING elections whose voting_opens_at has passed
         become ACTIVE (so voting can begin).
      2. CLOSE + TALLY elections past voting_closes_at: ACTIVE -> COMPLETED with
         the winner persisted to results, RegionalElection.winner_id AND the
         single-seat Region.{position}_id column (governor_id / ambassador_id)
         per SYSTEMS step 3 — exactly as the async tally_election does (a
         voided/inconclusive election leaves the seat untouched). A COMPLETED
         election is never re-tallied (the status filter excludes it).
      3. FINALIZE policies past voting_closes_at: VOTING -> {IMPLEMENTED |
         REJECTED}, applying a passed policy's effect onto the region CLAMPED to
         the CHECK bounds. Quorum/tally count distinct voters from the real
         regional_policy_votes ledger (migration c5a8e2f1b9d3), and a
         treasury-touching enactment writes a RegionalTreasuryEntry in the same
         per-row transaction — mirroring the async finalize_policy. A non-VOTING
         policy is never re-finalized.

    All logic is reimplemented SYNCHRONOUSLY here against the sync session and
    reuses the PURE, session-agnostic helpers in regional_governance_service
    (compute_quorum / quorum_pct_for_region / determine_election_winner /
    enact_changes_onto_region / threshold_for_policy /
    compute_treasury_adjustment) so the sweep applies IDENTICAL canon to the
    async vote-time path. We cannot await the async service methods here without
    poisoning the shared async engine pool — the same constraint that forces the
    faction/ARIA decay to be reimplemented in sync (see
    _apply_faction_decay_sync). Idempotent + a clean no-op when nothing is due.

    Returns {opened, tallied, enacted, rejected}.
    """
    from src.core.database import SessionLocal
    from src.models.region import (
        Region, RegionalElection, RegionalPolicy, RegionalVote,
        RegionalPolicyVote, RegionalTreasuryEntry,
        RegionalMembership, ElectionStatus, PolicyStatus,
    )
    from src.services.regional_governance_service import (
        compute_quorum, quorum_pct_for_region, threshold_for_policy,
        determine_election_winner, enact_changes_onto_region,
        compute_treasury_adjustment,
        ELECTION_TALLYING, POLICY_VOTERS_KEY,
    )
    from sqlalchemy import func as sa_func
    from sqlalchemy.orm.attributes import flag_modified

    result = {"opened": 0, "tallied": 0, "enacted": 0, "rejected": 0}
    now = datetime.utcnow()

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # --- Phase 1: open due PENDING elections -----------------------------
        due_open = (
            db.query(RegionalElection.id)
            .filter(
                RegionalElection.status == ElectionStatus.PENDING,
                RegionalElection.voting_opens_at <= now,
                RegionalElection.voting_closes_at > now,
            )
            .all()
        )
        for (eid,) in due_open:
            try:
                election = (
                    db.query(RegionalElection)
                    .filter(RegionalElection.id == eid)
                    .with_for_update()
                    .first()
                )
                if election is None or election.status != ElectionStatus.PENDING:
                    db.rollback()
                    continue
                election.status = ElectionStatus.ACTIVE
                db.commit()
                result["opened"] += 1
            except Exception:
                logger.exception("Governance sweep: open failed for election %s", eid)
                db.rollback()

        # --- Phase 2: close + tally elections past their window --------------
        due_close = (
            db.query(RegionalElection.id)
            .filter(
                RegionalElection.status == ElectionStatus.ACTIVE,
                RegionalElection.voting_closes_at <= now,
            )
            .all()
        )
        for (eid,) in due_close:
            try:
                election = (
                    db.query(RegionalElection)
                    .filter(RegionalElection.id == eid)
                    .with_for_update()
                    .first()
                )
                # Idempotency: skip anything that left ACTIVE since we listed it.
                if election is None or election.status != ElectionStatus.ACTIVE:
                    db.rollback()
                    continue
                region = (
                    db.query(Region)
                    .filter(Region.id == election.region_id)
                    .first()
                )
                if region is None:
                    db.rollback()
                    continue

                election.status = ELECTION_TALLYING
                rows = (
                    db.query(
                        RegionalVote.candidate_id,
                        sa_func.coalesce(sa_func.sum(RegionalVote.weight), 0),
                    )
                    .filter(RegionalVote.election_id == election.id)
                    .group_by(RegionalVote.candidate_id)
                    .all()
                )
                tallies = {str(cid): float(total) for cid, total in rows}
                winner, payload = determine_election_winner(region, election, tallies)
                if not tallies:
                    payload["inconclusive"] = True
                election.results = payload
                flag_modified(election, "results")

                # Persist the winner (SYSTEMS step 3), mirroring
                # tally_election: winner_id is the winning candidate's player_id,
                # or None when voided/inconclusive (no candidate cleared the
                # supermajority gate / no votes cast). A voided/inconclusive
                # election leaves the incumbent Region.{position}_id untouched
                # (a failed election does not vacate the seat).
                winner_uuid: Optional[uuid.UUID] = None
                if winner is not None:
                    try:
                        winner_uuid = uuid.UUID(str(winner))
                    except (TypeError, ValueError):
                        winner_uuid = None
                election.winner_id = winner_uuid
                if winner_uuid is not None:
                    # Region.{position}_id for single-seat positions
                    # (governor_id / ambassador_id). council_member is multi-seat
                    # and has no single-occupant column — it persists to the
                    # election row only.
                    position_column = f"{election.position}_id"
                    if hasattr(region, position_column):
                        setattr(region, position_column, winner_uuid)
                        region.updated_at = now

                election.status = ElectionStatus.COMPLETED
                db.commit()
                result["tallied"] += 1
            except Exception:
                logger.exception("Governance sweep: tally failed for election %s", eid)
                db.rollback()

        # --- Phase 3: finalize policies past their window --------------------
        due_policies = (
            db.query(RegionalPolicy.id)
            .filter(
                RegionalPolicy.status == PolicyStatus.VOTING,
                RegionalPolicy.voting_closes_at <= now,
            )
            .all()
        )
        for (pid,) in due_policies:
            try:
                policy = (
                    db.query(RegionalPolicy)
                    .filter(RegionalPolicy.id == pid)
                    .with_for_update()
                    .first()
                )
                # Idempotency: only a still-VOTING policy is finalized.
                if policy is None or policy.status != PolicyStatus.VOTING:
                    db.rollback()
                    continue
                region = (
                    db.query(Region)
                    .filter(Region.id == policy.region_id)
                    .with_for_update()
                    .first()
                )
                if region is None:
                    db.rollback()
                    continue

                eligible = (
                    db.query(sa_func.count(RegionalMembership.id))
                    .filter(
                        RegionalMembership.region_id == region.id,
                        RegionalMembership.membership_type.in_(["citizen", "resident"]),
                        RegionalMembership.voting_power > 0,
                    )
                    .scalar()
                ) or 0
                quorum = compute_quorum(int(eligible), quorum_pct_for_region(region))

                # Quorum denominator: number of distinct voters who actually
                # voted, counted from the real regional_policy_votes ledger
                # (migration c5a8e2f1b9d3), mirroring finalize_policy. Falls back
                # to the legacy proposed_changes['_voters'] list (then raw tally
                # presence) ONLY for legacy/manual rows predating the table —
                # strictly a backward-compat read; nothing writes _voters now.
                votes_cast = int(
                    db.query(sa_func.count(RegionalPolicyVote.id))
                    .filter(RegionalPolicyVote.policy_id == policy.id)
                    .scalar()
                    or 0
                )
                changes = dict(policy.proposed_changes or {})
                if votes_cast == 0:
                    legacy_voters = changes.get(POLICY_VOTERS_KEY)
                    votes_cast = (
                        len(legacy_voters) if isinstance(legacy_voters, list)
                        else (1 if (policy.votes_for or 0) + (policy.votes_against or 0) > 0 else 0)
                    )

                threshold = threshold_for_policy(region, policy.policy_type)
                total_weight = int(policy.votes_for or 0) + int(policy.votes_against or 0)
                approval = (
                    float(policy.votes_for or 0) / total_weight
                    if total_weight > 0 else 0.0
                )

                if votes_cast < quorum:
                    policy.status = PolicyStatus.REJECTED
                    db.commit()
                    result["rejected"] += 1
                elif approval >= float(threshold):
                    policy.status = PolicyStatus.PASSED
                    enact_changes_onto_region(region, policy.proposed_changes)
                    region.updated_at = now

                    # Treasury-touching enactment (ADR-0059 N-I4), mirroring
                    # finalize_policy: if the policy carries a treasury
                    # adjustment, mutate Region.treasury_balance and write a
                    # RegionalTreasuryEntry row in THIS SAME per-row transaction
                    # so the running balance stays reconcilable
                    # (SUM(delta) == treasury_balance). No current canon policy
                    # type carries it, so existing policies are unaffected.
                    treasury_delta = compute_treasury_adjustment(
                        region, policy.proposed_changes
                    )
                    if treasury_delta is not None:
                        before = int(region.treasury_balance or 0)
                        after = before + treasury_delta
                        region.treasury_balance = after
                        db.add(RegionalTreasuryEntry(
                            region_id=region.id,
                            before_balance=before,
                            after_balance=after,
                            delta=treasury_delta,
                            cause_type=RegionalTreasuryEntry.CAUSE_POLICY_ENACTMENT,
                            cause_id=policy.id,
                            reason=f"Policy enacted: {policy.title}",
                        ))

                    cleaned = dict(policy.proposed_changes or {})
                    cleaned.pop(POLICY_VOTERS_KEY, None)
                    policy.proposed_changes = cleaned
                    flag_modified(policy, "proposed_changes")
                    policy.status = PolicyStatus.IMPLEMENTED
                    db.commit()
                    result["enacted"] += 1
                else:
                    policy.status = PolicyStatus.REJECTED
                    db.commit()
                    result["rejected"] += 1
            except Exception:
                logger.exception("Governance sweep: finalize failed for policy %s", pid)
                db.rollback()

        # Final commit closes out any open (no-op) transaction so the advisory
        # lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Governance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Economy-metrics snapshot — daily galaxy-wide economic state writer
# ---------------------------------------------------------------------------

def _run_economic_metrics_snapshot_sync() -> Dict[str, Any]:
    """Write ONE daily EconomicMetrics row so the admin economy dashboard has
    real data instead of zeros.

    economy_analytics_service.get_economic_metrics() reads the most-recent
    EconomicMetrics row (``order_by(date.desc()).first()``) and surfaces four
    fields in the dashboard's "latest metrics" panel:
      - total_credits_in_circulation
      - total_trade_volume        (shown as "total_resources")
      - total_players_trading     (shown as "active_traders")
      - credit_velocity           (shown as "market_liquidity")
    Nothing ever WROTE an EconomicMetrics row, so that panel was permanently
    0/empty. This sweep populates exactly those fields (plus the cheap
    complementary columns) once per day.

    DISCIPLINE — mirrors the genesis/planetary/governance sweeps exactly:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-writing (and the lock auto-releases on commit/rollback);
      * commit releases the lock; failure is isolated (rolled back, logged,
        loop continues).

    IDEMPOTENCY — at most one snapshot per calendar day. The durable anchor is
    the unique, midnight-truncated ``EconomicMetrics.date`` column: we check for
    an existing row dated today (>= midnight UTC) BEFORE computing/inserting and
    no-op if present. The midnight truncation (rather than ``utcnow()``) is what
    makes the daily guard robust — two wakes on the same day resolve to the same
    ``date`` value, so the second is skipped (and, even if a race slipped past
    the check, the UNIQUE constraint on ``date`` would reject the duplicate,
    which the outer except rolls back without aborting the scheduler). This is
    the same durable-per-row-anchor pattern the weekly decay uses, keyed off a
    DB column instead of Galaxy.state.

    CIRCULATION — total_credits_in_circulation sums every credit pool the game
    actually tracks: active-player wallets (the analytics _calculate_money_supply
    number), NPC trader wallets (TRADER NPCs are full market actors —
    market_transaction.py), and the Region + Station treasuries (Integer credit
    pools). credits_in_player_accounts / credits_in_npc_accounts break that out.

    VOLUME — total_trade_volume / total_transactions / average_transaction_value
    come from the trailing-24h MarketTransaction window (the same window the
    analytics GDP/velocity calcs use), and credit_velocity = volume / money
    supply (mirroring _calculate_market_velocity), so the snapshot is internally
    consistent with the live-computed indicators on the same dashboard.

    Returns {"written": bool, "date": iso|None, "total_credits": int,
    "trade_volume": int, "active_traders": int}; written=False on the
    already-snapshotted / lock-held / no-galaxy no-op paths.
    """
    from src.core.database import SessionLocal
    from src.models.market_transaction import EconomicMetrics, MarketTransaction
    from src.models.npc_character import NPCCharacter
    from src.models.player import Player
    from src.models.region import Region
    from src.models.station import Station
    from sqlalchemy import func as sa_func

    not_written = {
        "written": False, "date": None,
        "total_credits": 0, "trade_volume": 0, "active_traders": 0,
    }

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_written

        # Durable daily anchor: midnight-truncated UTC. One row per calendar day.
        now = datetime.utcnow()
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        existing = (
            db.query(EconomicMetrics.id)
            .filter(EconomicMetrics.date >= today_midnight)
            .first()
        )
        if existing is not None:
            # Already snapshotted today — clean no-op (release the lock).
            db.commit()
            return not_written

        # --- Credit circulation -------------------------------------------
        player_credits = int(
            db.query(sa_func.coalesce(sa_func.sum(Player.credits), 0))
            .filter(Player.is_active.is_(True))
            .scalar() or 0
        )
        npc_credits = int(
            db.query(sa_func.coalesce(sa_func.sum(NPCCharacter.credits), 0))
            .scalar() or 0
        )
        region_treasury = int(
            db.query(sa_func.coalesce(sa_func.sum(Region.treasury_balance), 0))
            .scalar() or 0
        )
        station_treasury = int(
            db.query(sa_func.coalesce(sa_func.sum(Station.treasury_balance), 0))
            .scalar() or 0
        )
        total_credits = (
            player_credits + npc_credits + region_treasury + station_treasury
        )

        # --- Market volume (trailing 24h, same window as the analytics GDP) -
        window_start = now - timedelta(days=1)
        vol_row = (
            db.query(
                sa_func.coalesce(sa_func.sum(MarketTransaction.total_value), 0),
                sa_func.count(MarketTransaction.id),
            )
            .filter(MarketTransaction.timestamp >= window_start)
            .first()
        )
        total_trade_volume = int(vol_row[0] or 0) if vol_row else 0
        total_transactions = int(vol_row[1] or 0) if vol_row else 0
        avg_transaction_value = (
            float(total_trade_volume) / total_transactions
            if total_transactions > 0 else 0.0
        )

        # Distinct players that traded in the window (the dashboard's
        # "active_traders"); NPC trades carry npc_id, not player_id.
        active_traders = int(
            db.query(
                sa_func.count(sa_func.distinct(MarketTransaction.player_id))
            )
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.player_id.isnot(None),
            )
            .scalar() or 0
        )

        # Credit velocity = trailing-24h volume / money supply (active-player
        # credits), mirroring _calculate_market_velocity so the stored value
        # matches the live-computed one on the same dashboard.
        credit_velocity = (
            float(total_trade_volume) / player_credits
            if player_credits > 0 else 0.0
        )

        snapshot = EconomicMetrics(
            date=today_midnight,
            metric_type="daily",
            total_trade_volume=total_trade_volume,
            total_transactions=total_transactions,
            average_transaction_value=avg_transaction_value,
            total_credits_in_circulation=total_credits,
            credits_in_player_accounts=player_credits,
            credits_in_npc_accounts=npc_credits,
            credit_velocity=credit_velocity,
            total_players_trading=active_traders,
        )
        db.add(snapshot)
        db.commit()  # releases the xact lock

        logger.info(
            "Economy snapshot: %s — circulation=%d cr (player=%d npc=%d "
            "region=%d station=%d), 24h volume=%d cr over %d txn, "
            "active_traders=%d, velocity=%.4f",
            today_midnight.date().isoformat(), total_credits, player_credits,
            npc_credits, region_treasury, station_treasury, total_trade_volume,
            total_transactions, active_traders, credit_velocity,
        )
        return {
            "written": True,
            "date": today_midnight.isoformat(),
            "total_credits": total_credits,
            "trade_volume": total_trade_volume,
            "active_traders": active_traders,
        }
    except Exception:
        # Includes the unique-constraint race on EconomicMetrics.date: roll back
        # the duplicate insert; tomorrow's wake retries cleanly.
        logger.exception("Economy snapshot sweep failed")
        db.rollback()
        return not_written
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Host task — async wrapper + sync tick bodies
# ---------------------------------------------------------------------------

def _run_due_ticks_sync(elapsed_seconds: int) -> List[Dict[str, Any]]:
    """Run every loop whose cadence divides ``elapsed_seconds``.

    Locking: a DEDICATED lock session acquires pg_try_advisory_xact_lock
    (transaction-level). Transaction-level locks auto-release on commit or
    rollback — including when the session is returned to the connection pool
    — so they cannot get stuck on an idle pooled connection the way
    session-level pg_try_advisory_lock can. The lock session never commits
    its open transaction; closing it triggers a rollback which releases the
    lock cleanly regardless of what happens in the work sessions.

    Isolation: each loop gets its OWN fresh session and commits
    independently so a later loop's failure cannot roll back earlier work.
    """
    from src.core.database import SessionLocal

    events: List[Dict[str, Any]] = []

    # --- advisory lock on a dedicated session ---------------------------------
    lock_db = SessionLocal()
    try:
        got_lock = lock_db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: advisory lock held elsewhere — skipping tick")
            return []

        # Lock is now held for the duration of lock_db's open transaction.
        # Run each loop in its own session so per-loop commits don't touch
        # lock_db's transaction (and therefore don't release the lock early).
        from src.services.npc_engagement_service import sweep_pending_engagements

        # Monotonic Loop-A invocation index — drives the per-NPC patrol
        # stagger so a co-located squad disperses across its route instead of
        # advancing in unison. Resets on process restart; stable per-NPC
        # phases re-establish the spread within a few ticks regardless.
        loop_a_tick = elapsed_seconds // LOOP_A_SECONDS

        for cadence, loop_fn, label in (
            (ENGAGEMENT_SWEEP_SECONDS, sweep_pending_engagements, "engagement-sweep"),
            (LOOP_A_SECONDS, run_loop_a, "A"),
            (LOOP_B_SECONDS, run_loop_b, "B"),
            (LOOP_C_SECONDS, run_loop_c, "C"),
        ):
            if elapsed_seconds % cadence != 0:
                continue
            work_db = SessionLocal()
            try:
                loop_events = (
                    run_loop_a(work_db, loop_a_tick)
                    if label == "A"
                    else loop_fn(work_db)
                )
                work_db.commit()
                events.extend(loop_events)
            except Exception:
                logger.exception("NPC scheduler: Loop %s failed", label)
                work_db.rollback()
            finally:
                work_db.close()

    finally:
        # Closing lock_db without committing rolls back its transaction,
        # which releases the xact-level advisory lock automatically.
        lock_db.close()

    return events


def _repair_orphan_schedules_sync() -> int:
    """Give roster-less NPCs a patrol schedule so Loop A can drive them.

    Gated by the same xact-level advisory lock the ticks use, so when
    several gameserver workers boot together only one performs the repair
    (the others see the lock held and skip — the write is deterministic, but
    serializing it keeps the count honest and matches the scheduler's
    locking discipline). Idempotent: only empty-schedule rows are touched.
    """
    from src.core.database import SessionLocal
    from src.services.npc_spawn_service import backfill_orphan_npc_schedules

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        repaired = backfill_orphan_npc_schedules(db)
        db.commit()  # releases the xact lock
        return repaired
    finally:
        db.close()


def _seed_trader_rosters_sync() -> int:
    """Ensure merchant_captain NPCRoster rows exist for every galaxy so the
    NPC trader economy actually runs.

    seed_trader_rosters generates trader rosters from station topology (one
    per region with >=2 trading stations) — it is gameserver-side because
    BANG emits no trader kind. It was only ever invoked by the manual
    spawn_npcs.py CLI, so on a galaxy where that was never run there are zero
    trader rosters and Loop B never spawns merchant captains. Seeding it at
    scheduler startup makes the trader economy self-heal. Idempotent by
    bang_roster_ref; xact-advisory-lock-gated like the orphan repair.
    Returns the number of trader rosters created.
    """
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy
    from src.services.npc_spawn_service import seed_trader_rosters

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        # seed_trader_rosters loops ALL regions (it is not galaxy-scoped),
        # so it must be called exactly ONCE — calling it per-galaxy would
        # create one roster per region PER stale galaxy row. The galaxy.id is
        # only a namespace prefix on bang_roster_ref; the most-recent galaxy
        # is a stable choice that keeps the seed idempotent across restarts.
        galaxy = db.query(Galaxy).order_by(Galaxy.created_at.desc()).first()
        if galaxy is None:
            return 0
        stats = seed_trader_rosters(db, galaxy)
        db.commit()  # releases the xact lock
        return stats.get("trader_rosters_created", 0)
    finally:
        db.close()


def bootstrap_region_sync(region_id: uuid.UUID) -> Dict[str, Any]:
    """ADR-0069 Phase 12.5c post-commit hook: seed initial NPCs + rosters
    for a freshly-imported region from its materialized BANG snapshot.

    Called by the bang-import path (``BangImportService.run_generation_job``
    & friends) AFTER the import transaction commits — the sectors and the
    snapshot's ``region_id`` slot must already be visible for
    ``_region_offset_map`` to derive host-sector offsets. Runs in a worker
    thread with its own ``SessionLocal`` and commits its own transaction.

    Wraps :func:`npc_spawn_service.bootstrap_region`, which is the single
    idempotent entry point that materializes NPCs from the snapshot, seeds
    BANG-derived NPCRoster rows (``seed_rosters_from_bang``, keyed on the
    unique ``bang_roster_ref``), seeds topology-derived merchant_captain
    rosters (``seed_trader_rosters``, disjoint ``…:trader:…`` ref
    namespace), and backfills schedules. Because every underlying step is
    idempotent by ``bang_roster_ref``, this reconciles cleanly with the
    scheduler-boot ``_seed_trader_rosters_sync`` — whichever runs first
    creates the trader rosters; the other no-ops on the existing refs.

    Serialized against the scheduler's tick bodies via the same xact-level
    advisory lock. Unlike the boot repairs, this acquires the lock
    *blockingly* (``pg_advisory_xact_lock``) rather than skip-on-contention:
    a post-import bootstrap MUST run, so it waits for a concurrent tick to
    finish rather than silently dropping the seed.
    """
    from src.core.database import SessionLocal
    from src.services import npc_spawn_service

    db = SessionLocal()
    try:
        # Blocking acquire — the import just committed; we must seed, so we
        # wait for any in-flight tick rather than skip. Released on commit.
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        )
        stats = npc_spawn_service.bootstrap_region(db, region_id)
        db.commit()  # releases the xact lock
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _disperse_law_patrols_sync() -> int:
    """Scatter LAW_ENFORCEMENT NPCs across their region (deterministic per-NPC
    anchors) so they stop swarming the roster's single host sector. Idempotent +
    self-healing across restarts. xact-advisory-lock-gated. Returns dispersed."""
    from src.core.database import SessionLocal
    from src.services.npc_movement_service import disperse_law_patrols

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        count = disperse_law_patrols(db)
        db.commit()  # releases the xact lock
        return count
    finally:
        db.close()


def _relocate_stranded_npcs_sync() -> int:
    """Un-stick NPCs frozen in a sector that can't reach their route (the
    silent next_hop_toward→None no-op — e.g. a trader stranded in the wrong
    region after a galaxy re-bootstrap). Teleport-repairs each onto one of its
    own route sectors. Safe + idempotent (only genuinely stranded NPCs move);
    no roster/galaxy surgery. xact-advisory-lock-gated like the other repairs.
    Returns the number of NPCs relocated."""
    from src.core.database import SessionLocal
    from src.services.npc_movement_service import relocate_stranded_npcs

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        count = relocate_stranded_npcs(db)
        db.commit()  # releases the xact lock
        return count
    finally:
        db.close()


def _assign_trader_notoriety_sync() -> int:
    """Backfill notoriety onto pre-existing TRADER NPCs that have none (the
    column shipped after they spawned). Derived from each captain's existing
    persona title (+ a STABLE per-id jitter) so it's coherent with the name
    players see and deterministic across restarts. Idempotent: only NULL rows
    are touched. xact-advisory-lock-gated like the other startup repairs."""
    from src.core.database import SessionLocal
    from src.services.npc_spawn_service import notoriety_from_title

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        rows = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.archetype == NPCArchetype.TRADER,
                NPCCharacter.notoriety.is_(None),
            )
            .all()
        )
        for npc in rows:
            # Stable per-id jitter in [0,1) (hash of the uuid → fraction).
            u = (npc.id.int % 1000) / 1000.0
            npc.notoriety = notoriety_from_title(npc.title, u)
        if rows:
            db.commit()  # releases the xact lock
        return len(rows)
    finally:
        db.close()


def _assign_trader_missions_sync() -> Dict[str, int]:
    """Convert a share of existing commerce traders into colonist couriers and
    science vessels so the galaxy has purposeful missions immediately (new
    spawns already roll missions; this brings the pre-existing fleet along).
    Idempotent: converges to ~40% colonist / ~15% science and stops — only
    commerce traders (no 'mission' in their schedule) are ever reassigned."""
    from src.core.database import SessionLocal
    from src.services import npc_mission_service as MS

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return {}
        traders = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.archetype == NPCArchetype.TRADER,
                NPCCharacter.status == NPCStatus.ON_DUTY,
                NPCCharacter.ship_id.isnot(None),
            )
            .all()
        )
        if not traders:
            return {}

        def mission_of(t):
            return (t.daily_schedule or {}).get("mission")

        total = len(traders)
        need_col = max(0, int(total * 0.40) - sum(1 for t in traders if mission_of(t) == MS.COLONIST_MISSION))
        need_sci = max(0, int(total * 0.15) - sum(1 for t in traders if mission_of(t) == MS.SCIENCE_MISSION))
        if need_col == 0 and need_sci == 0:
            return {"colonist": 0, "science": 0}

        hub = MS._population_hub(db)
        anchor = hub.sector_id if hub is not None else traders[0].current_sector_id
        col_pool = [r for r in (MS.generate_colonist_route(db, anchor) for _ in range(6)) if r]
        sci_pool = [r for r in (MS.generate_science_route(db, anchor) for _ in range(6)) if r]

        convertible = [t for t in traders if mission_of(t) is None]
        assigned = {"colonist": 0, "science": 0}
        for t in convertible:
            if need_col > 0 and col_pool:
                t.daily_schedule = MS.build_mission_schedule(random.choice(col_pool), MS.COLONIST_MISSION)
                need_col -= 1
                assigned["colonist"] += 1
            elif need_sci > 0 and sci_pool:
                t.daily_schedule = MS.build_mission_schedule(random.choice(sci_pool), MS.SCIENCE_MISSION)
                need_sci -= 1
                assigned["science"] += 1
            if need_col == 0 and need_sci == 0:
                break
        db.commit()  # releases the xact lock
        return assigned
    finally:
        db.close()


def _bulk_fill_traders_sync() -> int:
    """Spawn merchant captains up to each trader roster's target_count in one
    pass, so the galaxy reaches its full trader population at boot instead of
    crawling there one-per-10min via Loop B. Idempotent (only fills the
    deficit) and xact-advisory-lock-gated like the other startup repairs.
    Runs AFTER _seed_trader_rosters_sync so rosters exist and carry the current
    target. Returns the number of traders spawned."""
    from src.core.database import SessionLocal

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0
        rosters = (
            db.query(NPCRoster)
            .filter(NPCRoster.default_archetype == NPCArchetype.TRADER)
            .all()
        )
        spawned = 0
        for roster in rosters:
            spawned += len(_fill_roster_deficit(db, roster, fill_all=True))
        db.commit()  # releases the xact lock
        return spawned
    finally:
        db.close()


async def _broadcast_events(events: List[Dict[str, Any]]) -> None:
    """Broadcast realtime events from the EVENT LOOP (never the worker
    thread). Sector routing is best-effort: sector_connections only
    tracks connect-time locations today, so polled players_present is
    the authoritative visibility path."""
    if not events:
        return
    from src.services.websocket_service import connection_manager

    for event in events:
        sector_id = event.get("sector_id")
        if sector_id is None:
            continue
        try:
            await connection_manager.broadcast_to_sector(int(sector_id), dict(event))
        except Exception:
            logger.exception("NPC scheduler: broadcast failed for %s", event.get("type"))


async def npc_scheduler_loop() -> None:
    """The lifespan-owned host task. Wakes every TICK_SECONDS, runs due
    tick bodies in a worker thread, broadcasts the returned events."""
    logger.info(
        "NPC scheduler started (Loop A %ds / Loop B %ds / Loop C %ds)",
        LOOP_A_SECONDS, LOOP_B_SECONDS, LOOP_C_SECONDS,
    )
    # One-time startup repair: NPCs spawned from a BANG snapshot that
    # carried no rosters have empty daily_schedules and would freeze in
    # PATROL (Loop A resolves no block for them). Give them patrol routes
    # so the world is actually alive. Idempotent + best-effort.
    try:
        repaired = await asyncio.to_thread(_repair_orphan_schedules_sync)
        if repaired:
            logger.info("NPC scheduler: repaired %d orphan NPC schedules", repaired)
    except Exception:
        logger.exception("NPC scheduler: orphan schedule repair failed")
    # Ensure trader rosters exist so Loop B spawns merchant captains and the
    # NPC trade economy runs (seed_trader_rosters was previously CLI-only).
    try:
        seeded = await asyncio.to_thread(_seed_trader_rosters_sync)
        if seeded:
            logger.info("NPC scheduler: seeded %d trader roster(s)", seeded)
    except Exception:
        logger.exception("NPC scheduler: trader roster seeding failed")
    # Bulk-fill trader rosters to target so the galaxy reaches its full
    # merchant population immediately rather than crawling via Loop B.
    try:
        filled = await asyncio.to_thread(_bulk_fill_traders_sync)
        if filled:
            logger.info("NPC scheduler: bulk-spawned %d trader(s) to target", filled)
    except Exception:
        logger.exception("NPC scheduler: trader bulk-fill failed")
    # Backfill notoriety onto traders that predate the column.
    try:
        scored = await asyncio.to_thread(_assign_trader_notoriety_sync)
        if scored:
            logger.info("NPC scheduler: assigned notoriety to %d trader(s)", scored)
    except Exception:
        logger.exception("NPC scheduler: trader notoriety backfill failed")
    # Give a share of the existing fleet colonist-courier / science missions.
    try:
        missions = await asyncio.to_thread(_assign_trader_missions_sync)
        if missions and (missions.get("colonist") or missions.get("science")):
            logger.info("NPC scheduler: assigned missions — %d colonist courier(s), %d science vessel(s)",
                        missions.get("colonist", 0), missions.get("science", 0))
    except Exception:
        logger.exception("NPC scheduler: trader mission assignment failed")
    # Un-stick NPCs stranded in sectors they can't path out of (e.g. left in
    # the wrong region by a galaxy re-bootstrap) so they resume moving.
    try:
        unstuck = await asyncio.to_thread(_relocate_stranded_npcs_sync)
        if unstuck:
            logger.info("NPC scheduler: relocated %d stranded NPC(s)", unstuck)
    except Exception:
        logger.exception("NPC scheduler: stranded-NPC relocation failed")
    # Disperse LAW patrols across their region (stop the single-host swarm).
    try:
        spread = await asyncio.to_thread(_disperse_law_patrols_sync)
        if spread:
            logger.info("NPC scheduler: dispersed %d LAW patrol(s)", spread)
    except Exception:
        logger.exception("NPC scheduler: LAW patrol dispersal failed")
    elapsed = 0
    while True:
        await asyncio.sleep(TICK_SECONDS)
        elapsed += TICK_SECONDS
        try:
            events = await asyncio.to_thread(_run_due_ticks_sync, elapsed)
            await _broadcast_events(events)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NPC scheduler: tick crashed (loop continues)")

        # Genesis formation completion sweep (every GENESIS_COMPLETION_SECONDS).
        # Makes the 48h formation timer authoritative for all planets, not just
        # those a player happens to read — runs in the worker thread, own
        # session, own advisory lock.
        if elapsed % GENESIS_COMPLETION_SECONDS == 0:
            try:
                completed = await asyncio.to_thread(_run_genesis_completion_sync)
                if completed:
                    logger.info(
                        "NPC scheduler: completed %d due genesis formation(s)",
                        completed,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "NPC scheduler: genesis completion sweep crashed (loop continues)"
                )

        # Planetary lazy-advance sweep (terraforming progress + siege turns).
        # Drives every terraforming project and besieged planet forward on the
        # canonical clock so progress no longer depends on a player happening to
        # read the planet — runs in the worker thread, own session, own advisory
        # lock. Idempotent + a no-op when nothing qualifies.
        if elapsed % PLANETARY_ADVANCE_SECONDS == 0:
            try:
                advanced = await asyncio.to_thread(_run_planetary_advance_sync)
                if (
                    advanced.get("terraforming")
                    or advanced.get("siege")
                    or advanced.get("production")
                ):
                    logger.info(
                        "NPC scheduler: planetary advance — %d terraforming, "
                        "%d siege, %d production planet(s) progressed",
                        advanced.get("terraforming", 0),
                        advanced.get("siege", 0),
                        advanced.get("production", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "NPC scheduler: planetary advance sweep crashed (loop continues)"
                )

        # Regional governance sweep (open/close elections + finalize policies).
        # Drives the democratic loop forward on the durable per-row voting
        # windows so an election/policy resolves even if no player happens to
        # read it — runs in the worker thread, own session, own advisory lock.
        # Idempotent + a no-op when nothing is due.
        if elapsed % GOVERNANCE_SWEEP_SECONDS == 0:
            try:
                gov = await asyncio.to_thread(_run_governance_sweep_sync)
                if (
                    gov.get("opened")
                    or gov.get("tallied")
                    or gov.get("enacted")
                    or gov.get("rejected")
                ):
                    logger.info(
                        "NPC scheduler: governance sweep — %d opened, "
                        "%d tallied, %d enacted, %d rejected",
                        gov.get("opened", 0), gov.get("tallied", 0),
                        gov.get("enacted", 0), gov.get("rejected", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "NPC scheduler: governance sweep crashed (loop continues)"
                )

        # Weekly reputation/relationship decay (fully synchronous). Gated by a
        # COARSE elapsed pre-filter (WEEKLY_DECAY_CHECK_SECONDS) so we don't take
        # the advisory lock + query Galaxy.state every 60s; the durable
        # canonical-week anchor inside _run_weekly_decay_sync is what actually
        # guarantees the real work runs at most once per canonical week,
        # restart-proof. The 15-min pre-filter is far finer than a week, so no
        # week is ever missed.
        if elapsed % WEEKLY_DECAY_CHECK_SECONDS == 0:
            try:
                decay = await asyncio.to_thread(_run_weekly_decay_sync)
                if decay.get("week", -1) >= 0:
                    logger.info(
                        "NPC scheduler: weekly decay applied (week %d) — "
                        "personal=%d faction=%d aria=%d",
                        decay["week"], decay["personal"],
                        decay["faction"], decay["aria"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: weekly decay crashed (loop continues)")

        # Economy faucets — reputation stipend + galactic-citizen subscription
        # perk.  Same coarse pre-filter / durable-anchor pattern as the weekly
        # decay; intentionally on a separate cadence (20 min) to avoid
        # colliding with the decay wake.  run_weekly_faucet_sync is fully
        # synchronous and self-gated on the shared advisory lock.
        if elapsed % FAUCET_CHECK_SECONDS == 0:
            try:
                from src.services.economy_faucet_service import run_weekly_faucet_sync
                faucet = await asyncio.to_thread(run_weekly_faucet_sync)
                if faucet.get("week", -1) >= 0:
                    logger.info(
                        "NPC scheduler: economy faucet fired (week %d) — "
                        "stipend=%d player(s), citizen_perk=%d citizen(s), "
                        "total=%d cr injected",
                        faucet["week"], faucet["stipend_grants"],
                        faucet["citizen_grants"], faucet["total_credits"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: economy faucet crashed (loop continues)")

        # Economy-metrics snapshot — write ONE daily EconomicMetrics row so the
        # admin economy dashboard reads real numbers instead of zeros (nothing
        # else ever writes that table). Coarse elapsed pre-filter (25 min) so we
        # don't probe the DB every 60s; the durable once-per-day guarantee comes
        # from the unique, midnight-truncated EconomicMetrics.date anchor inside
        # the sweep (restart-proof). Own session, own advisory lock, failure
        # isolated — same discipline as the genesis/planetary/governance sweeps.
        if elapsed % ECONOMY_SNAPSHOT_CHECK_SECONDS == 0:
            try:
                snap = await asyncio.to_thread(_run_economic_metrics_snapshot_sync)
                if snap.get("written"):
                    logger.info(
                        "NPC scheduler: economy snapshot written (%s) — "
                        "circulation=%d cr, 24h volume=%d cr, active_traders=%d",
                        snap.get("date"), snap.get("total_credits", 0),
                        snap.get("trade_volume", 0), snap.get("active_traders", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: economy snapshot crashed (loop continues)")
