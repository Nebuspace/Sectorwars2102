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

    events: List[Dict[str, Any]] = []
    for _ in range(spawn_count):
        npc_name = _next_name(db, roster)

        # Defaults: the kind's single hull, title and ship-name convention.
        # Traders override all three below for variety.
        daily_schedule: Dict[str, Any] = dict(roster.schedule_template or {})
        spawn_spec = spec
        spawn_title = cfg.title
        ship_name = cfg.ship_name_format.format(name=npc_name)

        if is_trader:
            from src.services import npc_trading_service

            # Traders get a PER-NPC schedule: a generated 2–4 station route
            # encoded as multi-day blocks. No complementary stations in the
            # region → no spawn (retried next pass as markets move).
            route = npc_trading_service.generate_trade_route(
                db, roster.region_id, roster.host_sector_id
            )
            if route is None:
                logger.info(
                    "Loop B: no complementary trade route in region %s — "
                    "trader spawn deferred", roster.region_id,
                )
                return events
            daily_schedule = npc_trading_service.build_trader_schedule(route)

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
            spawn_title = random.choice(TRADER_TITLES)
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
