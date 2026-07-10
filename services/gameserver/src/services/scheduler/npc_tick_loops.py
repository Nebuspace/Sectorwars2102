"""NPC per-tick simulation loops (WO-QUALITY-techdebt-scheduler-split).

Loop A (schedule executor), Loop B (roster maintenance), Loop C (off-duty
rotation frame + presence reconciliation) — the three per-tick NPC-simulation
loops dispatched every scheduler wake by
``scheduler.presence_helpers._run_due_ticks_sync``. Distinct from the
daily/weekly/etc SWEEPS living in the other scheduler submodules: these three
run NPCs forward in real time rather than driving a periodic maintenance pass.

Moved verbatim from the old ``npc_scheduler_service.py``.
"""

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
    TRADER_TITLES_BY_TIER,
    notoriety_tier,
    roll_notoriety,
    _build_npc_ship,
    _presence_entry,
    _roman,
)

from src.services.scheduler._common import (
    RECRUIT_STAGE_HOURS,
    SENIOR_TENURE_HOURS,
    SENIOR_COMBAT_BONUS,
    SENIOR_SCANNER_BONUS,
    DECORATED_COMBAT_PER_MEDAL,
    DECORATED_SCANNER_PER_MEDAL,
    GENOCIDE_KILL_THRESHOLD,
    GENOCIDE_WINDOW_MINUTES,
    GENOCIDE_RESPONSE_MINUTES,
    _LIVE_STATUSES,
    _SCHEDULABLE_STATUSES,
    canonical_minute_of_day,
    canonical_day_number,
    canonical_weekday,
    resolve_schedule_block,
)

logger = logging.getLogger(__name__)


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

    # Career advancement for already-active NPCs (npc-lifecycle.md lines
    # 139-140, 148-149):
    #   active  -> senior    at 90 canonical-days tenure (combat +5%, scanner +1)
    #   active/senior -> decorated  on faction-medal earn (buff scales with
    #                    medal count). DECORATED outranks SENIOR, so it is
    #                    evaluated last and wins.
    # Same tenure anchor the recruit->active pass uses (npc.spawned_at via
    # game_time.canonical_hours_since) — the model has no separate
    # recruited_at/created_at field; spawned_at is the canon tenure clock.
    careerists = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.lifecycle_stage.in_(
                (NPCLifecycleStage.ACTIVE, NPCLifecycleStage.SENIOR)
            ),
            NPCCharacter.status.in_(_LIVE_STATUSES),
        )
        .all()
    )
    for npc in careerists:
        try:
            _advance_career_stage(npc)
        except Exception:
            logger.exception(
                "Loop B: career-stage advance failed for NPC %s", npc.id
            )

    flooded_regions = _genocide_flood_regions(db, now, events)

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


def _decoration_count(npc: NPCCharacter) -> int:
    """Number of faction medals this NPC has earned, read from the canon
    ``role_history.decorations[]`` array (npc-lifecycle.md career-arc JSONB,
    lines 156-169). The medal-EARN side records rows here; this pass only
    reads the count to drive the DECORATED transition and its scaling buff."""
    history = npc.role_history or {}
    decorations = history.get("decorations") or []
    return len([d for d in decorations if isinstance(d, dict)])


def _apply_stat_buff(
    npc: NPCCharacter,
    combat_bonus: float,
    scanner_bonus: int,
    flavor: str,
) -> None:
    """Idempotent read-modify-write of the per-NPC stat modifiers + display
    flavor cue into the existing ``backstory`` JSONB (no migration: backstory
    is JSONB, default dict — the canon home for per-NPC skill/flavor data per
    DATA_MODELS/npcs.md backstory shape). Only writes when a value actually
    changes so a steady-state daily pass stays a no-op."""
    backstory = dict(npc.backstory or {})
    mods = dict(backstory.get("stat_modifiers") or {})

    combat_bonus = round(float(combat_bonus), 4)
    desired = {
        "combat_bonus": combat_bonus,
        "scanner_bonus": int(scanner_bonus),
    }
    changed = False
    if mods.get("combat_bonus") != desired["combat_bonus"]:
        mods["combat_bonus"] = desired["combat_bonus"]
        changed = True
    if mods.get("scanner_bonus") != desired["scanner_bonus"]:
        mods["scanner_bonus"] = desired["scanner_bonus"]
        changed = True
    if backstory.get("display_flavor") != flavor:
        backstory["display_flavor"] = flavor
        changed = True

    if changed:
        backstory["stat_modifiers"] = mods
        npc.backstory = backstory
        flag_modified(npc, "backstory")


def _advance_career_stage(npc: NPCCharacter) -> None:
    """Drive the active -> senior (tenure) and active/senior -> decorated
    (medal-earn) transitions for one already-active NPC, applying the canon
    stat buffs and updating the display flavor cue. DECORATED outranks SENIOR
    (npc-lifecycle.md:141), so a decorated NPC keeps the DECORATED stage and a
    medal-scaled buff even once it crosses the 90-day tenure line.

    Idempotent and additive: re-running the daily pass on an already-advanced
    NPC re-derives the same stage + buff and writes nothing new."""
    medals = _decoration_count(npc)

    # DECORATED — earned >= 1 faction medal. Wins over SENIOR. Buff scales
    # with medal count (canon: "Stat buff scales with medal count").
    if medals > 0:
        if npc.lifecycle_stage != NPCLifecycleStage.DECORATED:
            npc.lifecycle_stage = NPCLifecycleStage.DECORATED
        _apply_stat_buff(
            npc,
            combat_bonus=DECORATED_COMBAT_PER_MEDAL * medals,
            scanner_bonus=DECORATED_SCANNER_PER_MEDAL * medals,
            flavor="Decorated",
        )
        return

    # SENIOR — tenure >= 90 canonical days. Same tenure anchor as the
    # recruit->active pass (spawned_at via canonical_hours_since).
    if (
        npc.lifecycle_stage == NPCLifecycleStage.ACTIVE
        and npc.spawned_at
        and game_time.canonical_hours_since(npc.spawned_at) >= SENIOR_TENURE_HOURS
    ):
        npc.lifecycle_stage = NPCLifecycleStage.SENIOR

    if npc.lifecycle_stage == NPCLifecycleStage.SENIOR:
        _apply_stat_buff(
            npc,
            combat_bonus=SENIOR_COMBAT_BONUS,
            scanner_bonus=SENIOR_SCANNER_BONUS,
            flavor="Senior",
        )


def _genocide_flood_regions(
    db: Session, now: datetime, events: Optional[List[Dict[str, Any]]] = None
) -> set:
    """Region ids currently under the N-V4 rapid-recovery flood: ≥3
    law-enforcement KIAs inside a 30-minute window, response active for
    1 hour after the triggering kill.

    When ``events`` is supplied, a best-effort ``npc.coordinated_genocide_detected``
    realtime event dict is appended per newly-flooded region (N-V4). Like
    every tick-body event it is broadcast POST-COMMIT by ``_broadcast_events``
    on the event loop (never from this worker thread), so a WebSocket hiccup
    can never roll back the underlying flood detection / roster fill."""
    from src.models.npc_character import NPCArchetype, NPCDeathLog

    lookback = timedelta(
        minutes=GENOCIDE_WINDOW_MINUTES + GENOCIDE_RESPONSE_MINUTES
    )
    rows = (
        db.query(
            NPCDeathLog.home_region_id,
            NPCDeathLog.killed_at,
            NPCDeathLog.npc_id,
        )
        .join(NPCCharacter, NPCDeathLog.npc_id == NPCCharacter.id)
        .filter(
            NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
            NPCDeathLog.killed_at >= now - lookback,
            NPCDeathLog.home_region_id.isnot(None),
        )
        .order_by(NPCDeathLog.home_region_id, NPCDeathLog.killed_at)
        .all()
    )

    by_region: Dict[Any, List[tuple]] = {}
    for region_id, killed_at, npc_id in rows:
        if killed_at is not None and killed_at.tzinfo is None:
            killed_at = killed_at.replace(tzinfo=UTC)
        by_region.setdefault(region_id, []).append((killed_at, npc_id))

    flooded = set()
    window = timedelta(minutes=GENOCIDE_WINDOW_MINUTES)
    response = timedelta(minutes=GENOCIDE_RESPONSE_MINUTES)
    for region_id, kills in by_region.items():
        for i in range(len(kills) - GENOCIDE_KILL_THRESHOLD + 1):
            third_at = kills[i + GENOCIDE_KILL_THRESHOLD - 1][0]
            if third_at - kills[i][0] <= window and now - third_at <= response:
                flooded.add(region_id)
                logger.warning(
                    "npc.coordinated_genocide_detected: region %s — "
                    "rapid-recovery flood active (2x recruiting, "
                    "half-stage recruits)",
                    region_id,
                )
                if events is not None:
                    # KIAs inside the triggering window (the marshals whose
                    # rapid loss tripped the flood). Counted from the window
                    # opener forward across every kill within GENOCIDE_WINDOW.
                    window_kills = [
                        (k_at, k_id)
                        for (k_at, k_id) in kills[i:]
                        if k_at - kills[i][0] <= window
                    ]
                    events.append({
                        "type": "npc.coordinated_genocide_detected",
                        "region_id": str(region_id),
                        "kills_in_window": len(window_kills),
                        "window_seconds": int(window.total_seconds()),
                        "marshal_npc_ids": [
                            str(k_id) for (_, k_id) in window_kills
                            if k_id is not None
                        ],
                        "at": now.isoformat(),
                    })
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


