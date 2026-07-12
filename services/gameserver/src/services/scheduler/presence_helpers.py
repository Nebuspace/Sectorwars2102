"""Boot-time repairs + periodic maintenance sweeps
(WO-QUALITY-techdebt-scheduler-split).

The per-tick dispatch wrapper (``_run_due_ticks_sync``, driving Loop A/B/C
from ``npc_tick_loops``), the self-healing boot-time repair sweeps (orphan
schedule backfill, trader roster seed/bulk-fill, law-patrol dispersal,
stranded-NPC relocate, trader notoriety/mission backfill), and the periodic
maintenance sweeps that don't belong to any one economic/reputation/pirate
domain: player retention-signal scan, citizen-conditional ship re-bake,
stale-presence cleanup, ARIA storage prune, and route-optimization-run
retention (grouped here with the other telemetry-pruning sweeps rather than
in ``economy_sweeps``, for line-count balance).

Moved verbatim from the old ``npc_scheduler_service.py``.
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

from src.models.npc_character import (
    NPCArchetype,
    NPCCharacter,
    NPCRoster,
    NPCStatus,
)
from src.models.player import Player
from src.models.sector import Sector

from src.services.scheduler._common import (
    ENGAGEMENT_SWEEP_SECONDS,
    LOOP_A_SECONDS,
    LOOP_B_SECONDS,
    LOOP_C_SECONDS,
    _ARIA_PRUNE_STATE_KEY,
    _RETENTION_SWEEP_STATE_KEY,
    _CITIZEN_REBAKE_STATE_KEY,
    ROUTE_RUNS_RETENTION_DAYS,
    ROUTE_RUNS_RETENTION_MAX_PER_PLAYER,
    _ADVISORY_LOCK_KEY,
    _CITIZEN_REBAKE_LOCK_KEY,
    _PRESENCE_SWEEP_LOCK_KEY,
    PRESENCE_STALE_MINUTES,
    region_lock_key,
    _ROUTE_RUNS_RETENTION_LOCK_KEY,
    _ORPHAN_SCHEDULE_REPAIR_LOCK_KEY,
    _SEED_TRADER_ROSTERS_LOCK_KEY,
    _LAW_PATROL_DISPERSAL_LOCK_KEY,
    _STRANDED_RELOCATE_LOCK_KEY,
    _TRADER_NOTORIETY_LOCK_KEY,
    _TRADER_MISSION_LOCK_KEY,
    _BULK_FILL_TRADERS_LOCK_KEY,
    _RETENTION_SWEEP_LOCK_KEY,
    canonical_day_number,
)
from src.services.scheduler.npc_tick_loops import (
    run_loop_a,
    run_loop_b,
    run_loop_c,
    _fill_roster_deficit,
)

logger = logging.getLogger(__name__)


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
            {"key": _ORPHAN_SCHEDULE_REPAIR_LOCK_KEY},
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
            {"key": _SEED_TRADER_ROSTERS_LOCK_KEY},
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

    Locked on ``region_lock_key(region_id)`` (WO-RT-LOCK-ACTIVATE) rather
    than the global tick key — a bootstrap for region A no longer waits on
    an unrelated region's tick work or on any other sweep, and this is the
    guard's first production call site. Unlike the boot repairs, this
    acquires the lock *blockingly* (``pg_advisory_xact_lock``) rather than
    skip-on-contention: a post-import bootstrap MUST run, so it waits for a
    concurrent bootstrap of the SAME region (e.g. a duplicate import retry)
    to finish rather than silently dropping the seed or double-seeding it.
    """
    from src.core.database import SessionLocal
    from src.services import npc_spawn_service

    db = SessionLocal()
    try:
        # Blocking acquire, region-scoped — a same-region bootstrap already
        # in flight (e.g. a retried import) must be waited out, not skipped;
        # a different region's bootstrap or the main tick never contends
        # here at all. Released on commit.
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": region_lock_key(region_id)},
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
            {"key": _LAW_PATROL_DISPERSAL_LOCK_KEY},
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
            {"key": _STRANDED_RELOCATE_LOCK_KEY},
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
            {"key": _TRADER_NOTORIETY_LOCK_KEY},
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
            {"key": _TRADER_MISSION_LOCK_KEY},
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
            {"key": _BULK_FILL_TRADERS_LOCK_KEY},
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



def _run_retention_sweep_sync() -> Dict[str, int]:
    """Nightly at-risk retention-signal sweep (WO-RE2) — FULLY SYNCHRONOUS,
    day-gated on a durable canonical-day anchor in ``Galaxy.state``
    (``_RETENTION_SWEEP_STATE_KEY``), mirroring the ARIA-prune / G18-recompute
    discipline so the all-active-players scan runs at most ONCE per canonical day
    across process restarts. Run via ``asyncio.to_thread`` from the loop (the
    signal computer uses a SYNC Session — no AsyncSession, no event-loop bridge,
    so nothing can poison the shared async pool).

    READ-ONLY on the analytics tables: ``RetentionService.compute_player_signals``
    only SELECTs from PlayerActivity / PlayerSession / CombatLog / Message /
    Player. The ONLY write this sweep performs is upserting the single OPEN
    re-engagement-queue row per flagged player.

    Per-player failure isolation: each player gets its OWN savepoint
    (``db.begin_nested``); a compute or upsert error for one player is rolled
    back to that savepoint and skipped — it NEVER aborts the sweep or corrupts
    another player's flag (mirrors the per-row savepoint discipline in
    sweep_pending_engagements, WO-B1). The outer txn commits all surviving
    flags + the day anchor together at the end.

    Idempotent: re-running the same canonical day re-computes signals and
    refreshes each flagged player's OPEN row in place (signals/detail/computed_*
    updated); players who CLEARED all signals since a prior flag have their OPEN
    row RESOLVED. The day anchor short-circuits a second run in the same
    canonical day to a clean no-op.

    Returns {players_scanned, players_flagged, rows_resolved, day} (day=-1 + all
    zero when not yet due / lock held elsewhere)."""
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy
    from src.models.player import Player
    from src.models.player_analytics import PlayerReEngagement
    from src.services.retention_service import RetentionService

    this_day = canonical_day_number()
    not_due = {
        "players_scanned": 0,
        "players_flagged": 0,
        "rows_resolved": 0,
        "day": -1,
    }

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _RETENTION_SWEEP_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_due

        galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
        if galaxy is None:
            return not_due
        state = dict(galaxy.state or {})
        last_day = state.get(_RETENTION_SWEEP_STATE_KEY)
        if last_day is not None and int(last_day) >= this_day:
            return not_due  # already swept this canonical day

        now = datetime.now(UTC)
        svc = RetentionService(db)
        player_ids = [
            r[0]
            for r in db.query(Player.id).filter(Player.is_active.is_(True)).all()
        ]

        scanned = 0
        flagged = 0
        resolved = 0
        for pid in player_ids:
            scanned += 1
            try:
                # Per-player savepoint: a bad row rolls back to here and is
                # skipped without poisoning the outer transaction.
                with db.begin_nested():
                    verdict = svc.compute_player_signals(pid, now)
                    tripped = verdict["tripped"]
                    detail = verdict["detail"]

                    existing = (
                        db.query(PlayerReEngagement)
                        .filter(
                            PlayerReEngagement.player_id == pid,
                            PlayerReEngagement.status == "OPEN",
                        )
                        .first()
                    )

                    if tripped:
                        if existing is None:
                            db.add(
                                PlayerReEngagement(
                                    player_id=pid,
                                    signals=tripped,
                                    signal_detail=detail,
                                    status="OPEN",
                                    computed_at=now,
                                    computed_day=this_day,
                                )
                            )
                        else:
                            # Refresh the live OPEN flag in place.
                            existing.signals = tripped
                            existing.signal_detail = detail
                            existing.computed_at = now
                            existing.computed_day = this_day
                        flagged += 1
                    elif existing is not None:
                        # Player cleared all signals → close the open flag.
                        existing.status = "RESOLVED"
                        existing.resolved_at = now
                        resolved += 1
            except Exception:
                logger.exception(
                    "retention-sweep: signal compute/upsert failed for "
                    "player %s (skipped)", pid
                )
                # begin_nested already rolled the savepoint back on the raise;
                # the outer transaction is intact for the next player.

        # Advance the durable day anchor in the SAME outer txn as the flags.
        state = dict(galaxy.state or {})
        state[_RETENTION_SWEEP_STATE_KEY] = this_day
        galaxy.state = state
        flag_modified(galaxy, "state")
        db.commit()  # commits surviving flags + anchor atomically, releases lock

        logger.info(
            "retention-sweep: canonical day %d — scanned=%d flagged=%d "
            "resolved=%d", this_day, scanned, flagged, resolved,
        )
        return {
            "players_scanned": scanned,
            "players_flagged": flagged,
            "rows_resolved": resolved,
            "day": this_day,
        }
    except Exception:
        logger.exception(
            "retention-sweep: pass failed — day not advanced (idempotent retry "
            "next due wake)"
        )
        db.rollback()
        return not_due
    finally:
        db.close()


def _run_citizen_rebake_sweep_sync() -> Dict[str, int]:
    """Nightly citizen-conditional ship RE-BAKE sweep (WO-GC-C leg 4) — FULLY
    SYNCHRONOUS, day-gated on a durable canonical-day anchor in ``Galaxy.state``
    (``_CITIZEN_REBAKE_STATE_KEY``), mirroring _run_retention_sweep_sync EXACTLY
    in structure so the scan runs at most ONCE per canonical day across process
    restarts. Run via ``asyncio.to_thread`` from the loop (a SYNC Session — no
    AsyncSession, no event-loop bridge).

    THE FIREWALL (WO-GC-C): a lapsed Galactic-Citizen's citizen-conditional ship
    effects must go inert. The Citizen Clipper's slot 3 is a super, class-locked
    "maintenance" slot — the citizen PERK is that EXTRA slot existing at all. Its
    ``requires`` is now citizen-gated, and ``requires_satisfied`` evaluates the
    "citizen" case live against the owner's subscription. Re-baking each hull
    through the EXISTING bake path with the resolver live
    (``ShipUpgradeService._apply_module_effects``) therefore re-derives the baked
    stat columns: a LAPSED owner's citizen slot drops to a 0-stat contribution
    (hull persists + stays flyable; re-subscribe restores it on the next bake),
    while an ACTIVE citizen's slot is restored / left byte-identical (idempotent).
    A ≤24h re-bake lag is firewall-safe — the perk is capped utility, not
    power/income, so there is no exploit window worth a tighter trigger; this
    nightly sweep is the PRIMARY trigger.

    SCOPE (cheap): only hulls carrying a citizen-conditional slot. Today that is
    ``Ship.type == ShipType.CITIZEN_CLIPPER`` (skip destroyed hulls). Future
    citizen hulls get appended to this filter — the re-bake mechanism is hull-
    agnostic.

    The ONLY writes are to the re-baked ship rows: ``_apply_module_effects``
    mutates ``Ship.modules`` / ``Ship.modules['_baked']`` + the baked stat columns
    and does NOT commit — THIS sweep owns the commit. The baked-delta REPLACE
    contract (SM-3 §7.1: column = current − prev_baked + new_total, store _baked)
    is preserved untouched: re-baking with a now-inert slot simply re-runs that
    same REPLACE, so install→remove reversibility is unaffected.

    Per-ship failure isolation: each hull gets its OWN savepoint
    (``db.begin_nested``); a bad ship is rolled back to that savepoint and skipped
    — it NEVER aborts the sweep or corrupts another hull's bake (mirrors the
    per-row savepoint discipline in _run_retention_sweep_sync / WO-B1). The outer
    txn commits all surviving re-bakes + the day anchor together at the end.

    Idempotent: re-running the same canonical day re-bakes byte-identically for an
    active citizen (REPLACE with the same total is a no-op). The day anchor
    short-circuits a second run in the same canonical day to a clean no-op.

    DISTINCT advisory lock (``_CITIZEN_REBAKE_LOCK_KEY``, NOT the global
    ``_ADVISORY_LOCK_KEY``) so this serializes only against another concurrent
    re-bake pass.

    Returns {ships_scanned, ships_rebaked, day} (day=-1 + all zero when not yet
    due / lock held elsewhere)."""
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy
    from src.models.ship import Ship, ShipType
    from src.services.ship_upgrade_service import ShipUpgradeService

    this_day = canonical_day_number()
    not_due = {"ships_scanned": 0, "ships_rebaked": 0, "day": -1}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _CITIZEN_REBAKE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_due

        galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
        if galaxy is None:
            return not_due
        state = dict(galaxy.state or {})
        last_day = state.get(_CITIZEN_REBAKE_STATE_KEY)
        if last_day is not None and int(last_day) >= this_day:
            return not_due  # already re-baked this canonical day

        svc = ShipUpgradeService(db)
        # SCOPE: only hulls with a citizen-conditional slot. Append future
        # citizen hulls to this filter (the re-bake mechanism is hull-agnostic).
        ships = (
            db.query(Ship)
            .filter(
                Ship.type == ShipType.CITIZEN_CLIPPER,
                Ship.is_destroyed.is_(False),
            )
            .all()
        )

        scanned = 0
        rebaked = 0
        for ship in ships:
            scanned += 1
            try:
                # Per-ship savepoint: a bad hull rolls back to here and is
                # skipped without poisoning the outer transaction.
                with db.begin_nested():
                    # Re-bake through the EXISTING bake path with the resolver
                    # live — recomputes the citizen slot's contribution against
                    # the owner's CURRENT subscription (lapsed → 0, active →
                    # restored). Does NOT commit; the outer txn does.
                    svc._apply_module_effects(ship)
                rebaked += 1
            except Exception:
                logger.exception(
                    "citizen-rebake: re-bake failed for ship %s (skipped)",
                    getattr(ship, "id", "?"),
                )
                # begin_nested already rolled the savepoint back on the raise;
                # the outer transaction is intact for the next ship.

        # Advance the durable day anchor in the SAME outer txn as the re-bakes.
        state = dict(galaxy.state or {})
        state[_CITIZEN_REBAKE_STATE_KEY] = this_day
        galaxy.state = state
        flag_modified(galaxy, "state")
        db.commit()  # commits surviving re-bakes + anchor atomically, releases lock

        logger.info(
            "citizen-rebake: canonical day %d — scanned=%d rebaked=%d",
            this_day, scanned, rebaked,
        )
        return {
            "ships_scanned": scanned,
            "ships_rebaked": rebaked,
            "day": this_day,
        }
    except Exception:
        logger.exception(
            "citizen-rebake: pass failed — day not advanced (idempotent retry "
            "next due wake)"
        )
        db.rollback()
        return not_due
    finally:
        db.close()


def _run_presence_sweep_sync() -> Dict[str, int]:
    """WO-PRESWEEP — remove offline players from ``Sector.players_present``.

    A presence entry is written on movement (movement_service
    _update_player_presence) but only removed when the player MOVES again — so a
    player who logs out / goes idle lingers in the who's-here list forever. This
    sweep drops any entry whose player has not been active (``last_game_login`` —
    updated on turn-spend, turn_service.py) within ``PRESENCE_STALE_MINUTES``.

    DISCIPLINE: own SessionLocal; xact advisory lock so a 2nd instance skips;
    candidate query is COLUMN-ONLY (``Sector.id`` alone, filtered on non-empty
    presence) — it never loads a full ``Sector`` entity, so nothing here can go
    stale in this session's identity map by the time the per-sector lock below
    re-fetches it. Each candidate is then locked with
    ``.populate_existing().with_for_update()`` (TICKET-PRESENCE-PRUNE-LOCK)
    RIGHT BEFORE the read-modify-write, mirroring the exact chain shape
    ``movement_service._update_player_presence`` and ``npc_movement_service``'s
    roster movers already use on this SAME column (see
    test_movement_presence_lock_identity_map.py for the identity-map
    precedent this generalizes) — every writer to ``players_present`` now
    takes this same row lock before its RMW, so Postgres serializes concurrent
    writers instead of letting whichever commits last blindly overwrite the
    other's addition with a stale snapshot (the lost-update this ticket
    closes). ``.populate_existing()`` is defense-in-depth here specifically —
    the column-only candidate scan above never caches a stale full entity to
    begin with, so within THIS function there is nothing for it to refresh
    today; it is kept to match the house chain shape and to guard a future
    candidate-query change back to a full-entity SELECT from silently
    reintroducing the staleness class of bug. Per-sector commit + isolated
    try/except. IDEMPOTENT — re-removing an already-absent player is a no-op,
    so the advisory-lock-releases-on-first-commit property is harmless here.
    Reads a wall-clock last-seen, mutates only the JSONB list. No migration,
    no new row.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy.orm.attributes import flag_modified
    from src.core.database import SessionLocal

    db = SessionLocal()
    swept = 0
    sectors_touched = 0
    try:
        got = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PRESENCE_SWEEP_LOCK_KEY},
        ).scalar()
        if not got:
            db.rollback()
            return {"presence_entries_swept": 0, "sectors": 0}
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=PRESENCE_STALE_MINUTES)
        # Column-only candidate scan — deliberately selects ONLY Sector.id,
        # never the full entity, so this query cannot leave a soon-to-be-
        # stale Sector object cached in this session's identity map (see
        # the docstring above).
        candidate_ids = [
            row[0]
            for row in (
                db.query(Sector.id)
                .filter(text("jsonb_array_length(players_present) > 0"))
                .all()
            )
        ]
        for sector_pk in candidate_ids:
            try:
                # Lock RIGHT BEFORE the RMW (TICKET-PRESENCE-PRUNE-LOCK) — a
                # concurrent presence writer (movement_service.
                # _update_player_presence, npc_movement_service's roster
                # movers) takes this SAME .populate_existing().with_for_
                # update() chain on this SAME row before its own RMW;
                # Postgres now serializes the two instead of letting a blind
                # UPDATE from one clobber the other's addition.
                sec = (
                    db.query(Sector)
                    .filter(Sector.id == sector_pk)
                    .populate_existing()
                    .with_for_update()
                    .first()
                )
                if sec is None:
                    db.rollback()
                    continue
                entries = list(sec.players_present or [])
                pids = [
                    e.get("player_id") for e in entries
                    if isinstance(e, dict) and e.get("player_id")
                ]
                if not pids:
                    db.rollback()
                    continue
                rows = (
                    db.query(Player.id, Player.last_game_login)
                    .filter(Player.id.in_(pids))
                    .all()
                )
                fresh = {
                    str(pid) for pid, lgl in rows
                    if lgl is not None and lgl >= cutoff
                }
                kept = [e for e in entries if e.get("player_id") in fresh]
                removed = len(entries) - len(kept)
                if removed > 0:
                    sec.players_present = kept
                    flag_modified(sec, "players_present")
                    db.commit()
                    swept += removed
                    sectors_touched += 1
                else:
                    db.rollback()
            except Exception:
                db.rollback()
                logger.exception(
                    "presence sweep: sector %s failed (loop continues)",
                    sector_pk,
                )
        return {"presence_entries_swept": swept, "sectors": sectors_touched}
    finally:
        db.close()


async def _run_aria_prune_async() -> Dict[str, int]:
    """ASYNC daily ARIA storage-prune pass (WO-F16).

    Wires the dormant prune kernel
    (``ARIAPersonalIntelligenceService.prune_player_storage``) into the
    scheduler. For each player that HAS any ARIA storage, the kernel computes
    that player's combined ``ARIAPersonalMemory`` (memory_content) +
    ``ARIAMarketIntelligence`` (price_observations) JSON byte size and, if it
    exceeds the per-player hard cap (MAX_PLAYER_ARIA_BYTES — NO-CANON 10 MiB),
    evicts the OLDEST rows across BOTH tables until back under the cap.
    Under-cap players are left untouched.

    WHY ASYNC (and NOT a to_thread sync sweep like the other daily passes):
    ``prune_player_storage`` is an ``async def`` that owns its own per-player
    commit against an ``AsyncSession``. Running it through ``asyncio.to_thread``
    would execute a coroutine in a worker thread that has no running event loop
    — it would never actually run. So this pass opens its OWN async session
    (``AsyncSessionLocal`` from src.core.database, the same factory
    ``get_async_session`` yields from) and is ``await``-ed DIRECTLY by
    ``npc_scheduler_loop`` (no to_thread). It does NOT touch the sync engine /
    advisory-lock path the to_thread sweeps use, so there is no cross-engine
    pool contamination.

    Day-gating: a durable Galaxy.state JSONB anchor (``_ARIA_PRUNE_STATE_KEY``)
    holds the canonical-day index of the last completed prune, mirroring G18's
    ``_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY`` discipline so the all-players scan
    runs at most ONCE per canonical day across process restarts. canonical_day_
    number() is called NO-ARG (defaults to an aware datetime.now(UTC)) so the
    anchor never shifts on a naive/local-time interpretation.

    Best-effort per player: one player's prune error is logged and skipped — it
    never aborts the rest of the pass. The day anchor is advanced only after the
    per-player loop (a failure to advance just means a harmless idempotent
    re-run next pass — under-cap players are no-ops and an already-pruned player
    is simply under cap again).

    Returns {players_scanned, players_pruned, rows_evicted}.
    """
    from src.core.database import AsyncSessionLocal
    from src.models.galaxy import Galaxy
    from src.models.aria_personal_intelligence import (
        ARIAPersonalMemory, ARIAMarketIntelligence,
    )
    from src.services.aria_personal_intelligence_service import (
        ARIAPersonalIntelligenceService,
    )
    from sqlalchemy import select as sa_select
    from sqlalchemy.orm.attributes import flag_modified

    result = {"players_scanned": 0, "players_pruned": 0, "rows_evicted": 0}

    async with AsyncSessionLocal() as db:
        try:
            # --- Day-gate via durable Galaxy.state anchor --------------------
            # No-arg → aware datetime.now(UTC); mirrors the G18 recompute and
            # _run_weekly_decay_sync's canonical-clock anchor reads.
            this_day = canonical_day_number()
            galaxy_res = await db.execute(
                sa_select(Galaxy).order_by(Galaxy.created_at.asc())
            )
            galaxy = galaxy_res.scalars().first()
            gstate = dict(galaxy.state or {}) if galaxy is not None else {}
            last_day = gstate.get(_ARIA_PRUNE_STATE_KEY)
            already_today = (
                galaxy is not None
                and last_day is not None
                and int(last_day) >= this_day
            )
            if already_today:
                return result  # clean no-op — already pruned this canonical day

            # --- Enumerate ONLY players who HAVE ARIA storage ----------------
            # The kernel reads ARIAPersonalMemory + ARIAMarketIntelligence; a
            # player with neither has nothing to prune, so collect the DISTINCT
            # player_ids that own at least one row in EITHER table and merge
            # them (never scan all players blindly). Two cheap DISTINCT probes
            # unioned in Python — unambiguous and equally targeted.
            mem_ids_res = await db.execute(
                sa_select(ARIAPersonalMemory.player_id).distinct()
            )
            intel_ids_res = await db.execute(
                sa_select(ARIAMarketIntelligence.player_id).distinct()
            )
            player_ids = {
                pid for (pid,) in mem_ids_res.all() if pid is not None
            } | {
                pid for (pid,) in intel_ids_res.all() if pid is not None
            }

            service = ARIAPersonalIntelligenceService()

            # --- Best-effort per-player prune --------------------------------
            for pid in player_ids:
                result["players_scanned"] += 1
                try:
                    summary = await service.prune_player_storage(str(pid), db)
                    evicted = int(summary.get("evicted_total", 0)) if summary else 0
                    if evicted:
                        result["players_pruned"] += 1
                        result["rows_evicted"] += evicted
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "ARIA prune: prune failed for player %s (skipped)", pid
                    )
                    # One bad row owns its session; make sure a poisoned
                    # transaction can't break the next player's commit.
                    try:
                        await db.rollback()
                    except Exception:
                        logger.exception(
                            "ARIA prune: rollback failed after player %s", pid
                        )

            # --- Advance the durable per-day anchor (best-effort) ------------
            if galaxy is not None:
                try:
                    # Reuse the gstate dict captured BEFORE the per-player loop —
                    # do NOT re-read galaxy.state here. The kernel's per-player
                    # await db.commit() expires every ORM object on this async
                    # session (AsyncSessionLocal uses expire_on_commit=True), and
                    # a lazy re-read of an expired attribute on an async session
                    # raises MissingGreenlet (greenlet_spawn) — see
                    # enhanced_ai_service.py:477. Setting the attribute (no read)
                    # is safe; the captured dict carries the prior state.
                    gstate[_ARIA_PRUNE_STATE_KEY] = this_day
                    galaxy.state = gstate
                    flag_modified(galaxy, "state")
                    await db.commit()
                except Exception:
                    logger.exception(
                        "ARIA prune: day-anchor advance failed "
                        "(prune will re-run next pass; it is idempotent)"
                    )
                    await db.rollback()

            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ARIA prune: pass failed")
            try:
                await db.rollback()
            except Exception:
                logger.debug("ARIA prune: rollback failed after pass-level failure", exc_info=True)
            return result


# ---------------------------------------------------------------------------
# Route-optimization-run retention sweep (WO-OPS-ROUTE-RUNS-RETENTION) --
# grouped with the other periodic telemetry-pruning sweeps above rather than
# with economy_sweeps, for line-count balance (see module docstring).
# ---------------------------------------------------------------------------

def prune_route_optimization_runs(
    db: Session,
    *,
    now: Optional[datetime] = None,
    batch_size: int = 500,
) -> int:
    """Core RouteOptimizationRun retention logic (WO-OPS-ROUTE-RUNS-RETENTION).

    The table is written on every successful player route-optimize call
    (route_optimizer.py / ai.py, ``_record_optimization_run``) with no cap
    and no prune job — a spammy player can grow it unboundedly. This prunes
    a row only when BOTH of the following hold:

      * it is older than ``ROUTE_RUNS_RETENTION_DAYS``, AND
      * it is not among that player's ``ROUTE_RUNS_RETENTION_MAX_PER_PLAYER``
        most-recent rows (ranked by ``created_at`` desc).

    A player's newest K rows always survive regardless of age (a low-volume
    player's whole history is kept even once stale); any row inside the age
    window always survives regardless of rank (a high-volume player's recent
    activity is never pruned early just for exceeding K). Only a row that is
    BOTH stale AND beyond the per-player cap is eligible.

    Deliberately takes an injected ``db`` and does no session lifecycle,
    advisory-lock, or commit/rollback of its own (mirrors
    ``sweep_price_history``) — that discipline lives in the
    ``_run_route_runs_retention_sync`` wrapper below, which is also what
    makes this directly unit-testable against a session double.

    Ranking is done per-player, and only for players who have at least one
    row older than the cutoff (an indexed ``created_at`` filter, not a
    full-table scan) — a quiet table with no stale rows costs one cheap
    DISTINCT query and touches nothing else. Deletes are collected and
    applied in chunks of ``batch_size`` (default 500) rather than one
    unbounded statement. Idempotent: a second call after a full prune finds
    no stale rows left and deletes nothing.

    Returns the number of rows deleted.
    """
    from src.models.route_optimization_run import RouteOptimizationRun

    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=ROUTE_RUNS_RETENTION_DAYS)

    stale_player_ids = [
        row[0]
        for row in (
            db.query(RouteOptimizationRun.player_id)
            .filter(RouteOptimizationRun.created_at < cutoff)
            .distinct()
            .all()
        )
    ]
    if not stale_player_ids:
        return 0

    to_delete: List[Any] = []
    for player_id in stale_player_ids:
        rows = (
            db.query(RouteOptimizationRun.id, RouteOptimizationRun.created_at)
            .filter(RouteOptimizationRun.player_id == player_id)
            .order_by(RouteOptimizationRun.created_at.desc())
            .all()
        )
        for rank, (run_id, created_at) in enumerate(rows, start=1):
            if rank > ROUTE_RUNS_RETENTION_MAX_PER_PLAYER and created_at < cutoff:
                to_delete.append(run_id)

    deleted = 0
    for start in range(0, len(to_delete), batch_size):
        batch = to_delete[start:start + batch_size]
        deleted += (
            db.query(RouteOptimizationRun)
            .filter(RouteOptimizationRun.id.in_(batch))
            .delete(synchronize_session=False)
        )

    return deleted


def _run_route_runs_retention_sync() -> Dict[str, int]:
    """Own-session wrapper around ``prune_route_optimization_runs`` —
    SessionLocal + advisory lock + commit/rollback, same discipline as
    ``_run_price_history_sweep_sync``. A second gameserver instance racing
    the same tick skips (pg_try_advisory_xact_lock) rather than
    double-pruning; a mid-pass failure rolls back the whole batch (nothing
    partially deleted — the next daily wake retries cleanly)."""
    from src.core.database import SessionLocal

    not_pruned = {"deleted": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ROUTE_RUNS_RETENTION_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_pruned

        deleted = prune_route_optimization_runs(db)
        db.commit()  # releases the xact lock
        return {"deleted": deleted}
    except Exception:
        logger.exception("Route-optimization-run retention sweep failed")
        db.rollback()
        return not_pruned
    finally:
        db.close()

