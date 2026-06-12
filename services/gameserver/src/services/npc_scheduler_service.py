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
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.npc_character import (
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
    _build_npc_ship,
    _presence_entry,
    _roman,
)

logger = logging.getLogger(__name__)

# Wake interval of the host task; loop cadences must be multiples.
TICK_SECONDS = 60
LOOP_A_SECONDS = 5 * 60
LOOP_B_SECONDS = 10 * 60
LOOP_C_SECONDS = 30 * 60

# Session-level advisory lock key (pg_try_advisory_xact_lock argument).
_ADVISORY_LOCK_KEY = 0x53573231  # 'SW21'

# ADR-0063: recruit lifecycle stage lasts 7 canonical days, then ACTIVE.
RECRUIT_STAGE_HOURS = 7 * 24

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


def canonical_weekday(now: Optional[datetime] = None) -> int:
    """Canonical weekday, Monday=0 (matches datetime.weekday() at scale
    1.0 — 1970-01-01 was a Thursday)."""
    now = now or datetime.now(UTC)
    canonical_day = int(now.timestamp() * game_time.GAME_TIME_SCALE // 86400)
    return (canonical_day + 3) % 7


def resolve_schedule_block(
    daily_schedule: Dict[str, Any],
    minute: int,
    weekday: int,
) -> Optional[Dict[str, Any]]:
    """The schedule block covering ``minute``, honoring weekly_overrides
    (SYSTEMS/npc-lifecycle.md JSONB shape). None when nothing matches."""
    if not daily_schedule:
        return None
    shift = int(daily_schedule.get("shift_offset_hours") or 0)
    minute = (minute + shift * 60) % 1440

    blocks = daily_schedule.get("blocks") or []
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

def run_loop_a(db: Session) -> List[Dict[str, Any]]:
    """Resolve schedule blocks, transition activities, move patrollers."""
    events: List[Dict[str, Any]] = []
    now = datetime.now(UTC)
    minute = canonical_minute_of_day(now)
    weekday = canonical_weekday(now)

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

    for npc in npcs:
        block = resolve_schedule_block(npc.daily_schedule or {}, minute, weekday)
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

        # Movement is only driven for patrol blocks in this phase; other
        # location types (station, home_sector, lodging) no-op gracefully
        # until their slices land.
        if (
            activity == NPCActivity.PATROL
            and npc.status == NPCStatus.ON_DUTY
            and str(block.get("location_type", "")) == "patrol_route"
        ):
            try:
                events.extend(_drive_patrol(db, npc, block, minute))
            except Exception:
                logger.exception("Loop A: patrol drive failed for NPC %s", npc.id)
                db.rollback()

    db.flush()
    return events


def _drive_patrol(
    db: Session,
    npc: NPCCharacter,
    block: Dict[str, Any],
    minute: int,
) -> List[Dict[str, Any]]:
    """Move a patrolling NPC toward its route's current waypoint (single
    hop per call; npc_movement_service enforces canon transit pacing)."""
    ref = block.get("location_ref")
    if isinstance(ref, dict):
        sectors = [int(s) for s in (ref.get("sectors") or [])]
        minutes_per_sector = int(ref.get("minutes_per_sector") or 240)
    elif isinstance(ref, list):
        sectors = [int(s) for s in ref]
        minutes_per_sector = 240
    else:
        return []
    if not sectors or npc.current_sector_id is None:
        return []

    start = int(block.get("start_minute", 0))
    minutes_into = max(0, minute - start)
    desired = sectors[(minutes_into // max(1, minutes_per_sector)) % len(sectors)]
    if npc.current_sector_id == desired:
        return []

    next_hop = npc_movement_service.next_hop_toward(
        db, npc.current_sector_id, desired
    )
    if next_hop is None:
        return []
    return npc_movement_service.move_npc(db, npc, next_hop)


# ---------------------------------------------------------------------------
# Loop B — roster maintenance
# ---------------------------------------------------------------------------

def run_loop_b(db: Session) -> List[Dict[str, Any]]:
    """Fill roster deficits (immediate recruit fill, ADR-0063) and
    promote recruits whose stage elapsed."""
    events: List[Dict[str, Any]] = []

    # Recruit → active promotion (ADR-0063: 7 canonical days).
    recruits = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.lifecycle_stage == NPCLifecycleStage.RECRUIT,
            NPCCharacter.status.in_(_LIVE_STATUSES),
        )
        .all()
    )
    for npc in recruits:
        anchor = npc.spawned_at
        if anchor and game_time.canonical_hours_since(anchor) >= RECRUIT_STAGE_HOURS:
            npc.lifecycle_stage = NPCLifecycleStage.ACTIVE

    for roster in db.query(NPCRoster).all():
        try:
            spawned = _fill_roster_deficit(db, roster)
            events.extend(spawned)
        except Exception:
            logger.exception("Loop B: roster fill failed for %s", roster.id)
            db.rollback()

    db.flush()
    return events


def _fill_roster_deficit(db: Session, roster: NPCRoster) -> List[Dict[str, Any]]:
    """Spawn ONE replacement when the roster is under target (canon Loop
    B spawns one per pass — a wiped squad refills over successive
    passes, not instantaneously)."""
    cfg = KIND_CONFIG.get(roster.role)
    if cfg is None:
        # Roles without a spawn recipe yet (later slices) are tolerated.
        return []

    live = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.bang_roster_ref == roster.bang_roster_ref,
            NPCCharacter.status.in_(_LIVE_STATUSES),
        )
        .count()
    )
    if live >= roster.target_count:
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

    npc_name = _next_name(db, roster)
    now = datetime.now(UTC)

    ship = _build_npc_ship(
        spec,
        name=cfg.ship_name_format.format(name=npc_name),
        sector_id=roster.host_sector_id,
    )
    db.add(ship)
    db.flush()

    npc = NPCCharacter(
        name=npc_name,
        title=cfg.title,
        faction_code=roster.faction_code,
        archetype=roster.default_archetype,
        status=NPCStatus.ON_DUTY,
        current_sector_id=roster.host_sector_id,
        ship_id=ship.id,
        bang_roster_ref=roster.bang_roster_ref,
        home_region_id=roster.region_id,
        current_activity=NPCActivity.PATROL,
        # ADR-0063: successors spawn immediately as reduced-stat recruits.
        lifecycle_stage=NPCLifecycleStage.RECRUIT,
        daily_schedule=dict(roster.schedule_template or {}),
        engagement_eligible_at=now,
        spawned_at=now,
        last_seen_at=now,
    )
    db.add(npc)
    db.flush()

    npc_movement_service.add_npc_presence(sector, npc, ship)
    _join_squad(sector, cfg, roster, npc)
    db.flush()

    logger.info(
        "Loop B: spawned recruit %s for roster %s (%d/%d live)",
        npc.display_name, roster.bang_roster_ref, live + 1, roster.target_count,
    )
    return [{
        "type": "npc_spawned",
        "sector_id": roster.host_sector_id,
        "npc_id": str(npc.id),
        "display_name": npc.display_name,
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.name,
        "is_npc": True,
        "timestamp": now.isoformat(),
    }]


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
    """Run every loop whose cadence divides ``elapsed_seconds``. One
    session per tick; commit per loop so a later loop's failure cannot
    roll back an earlier loop's work."""
    from src.core.database import SessionLocal

    events: List[Dict[str, Any]] = []
    db = SessionLocal()
    try:
        # Multi-instance guard (session advisory lock, auto-released on
        # session close). A sibling instance skips its tick.
        got_lock = db.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: advisory lock held elsewhere — skipping tick")
            return []

        try:
            for cadence, loop_fn, label in (
                (LOOP_A_SECONDS, run_loop_a, "A"),
                (LOOP_B_SECONDS, run_loop_b, "B"),
                (LOOP_C_SECONDS, run_loop_c, "C"),
            ):
                if elapsed_seconds % cadence != 0:
                    continue
                try:
                    loop_events = loop_fn(db)
                    db.commit()
                    events.extend(loop_events)
                except Exception:
                    logger.exception("NPC scheduler: Loop %s failed", label)
                    db.rollback()
        finally:
            db.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _ADVISORY_LOCK_KEY},
            )
            db.commit()
    finally:
        db.close()
    return events


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
