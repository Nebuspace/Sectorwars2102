"""Host task — async wrapper + dispatch loop
(WO-QUALITY-techdebt-scheduler-split).

The decoupled NPC contract-generation task, ``npc_scheduler_loop`` (the
asyncio task main.py's lifespan creates), and ``_npc_scheduler_main_loop``'s
task-spawn/cancel lifecycle. This is the master dispatcher: it imports and
invokes essentially every sweep function from every other scheduler
submodule on its coarse elapsed-seconds pre-filter cadence.

Moved verbatim from the old ``npc_scheduler_service.py`` — see the original
module docstring (reproduced in ``src/services/scheduler/__init__.py``) for
the scheduler's overall Loop A/B/C + sweep design.
"""

import asyncio
import logging
import threading

from src.services.scheduler._common import (
    TICK_SECONDS,
    LOOP_A_SECONDS,
    LOOP_B_SECONDS,
    LOOP_C_SECONDS,
    GENESIS_COMPLETION_SECONDS,
    PLANETARY_ADVANCE_SECONDS,
    GOVERNANCE_SWEEP_SECONDS,
    CONSTRUCTION_ADVANCE_CHECK_SECONDS,
    ARIA_PRUNE_CHECK_SECONDS,
    RETENTION_SWEEP_CHECK_SECONDS,
    CITIZEN_REBAKE_CHECK_SECONDS,
    PIRATE_ECOSYSTEM_TICK_CHECK_SECONDS,
    WEEKLY_DECAY_CHECK_SECONDS,
    FAUCET_CHECK_SECONDS,
    ECONOMY_SNAPSHOT_CHECK_SECONDS,
    IDLE_INCOME_CHECK_SECONDS,
    DAILY_STIPEND_CHECK_SECONDS,
    BOUNTY_ACCRUAL_CHECK_SECONDS,
    PORT_OPERATING_COST_CHECK_SECONDS,
    STATION_RECOVERY_CHECK_SECONDS,
    RECLAIM_FLAG_CHECK_SECONDS,
    SUSTAINED_REP_DRIP_CHECK_SECONDS,
    PRICE_RECOMPUTE_FLUSH_SECONDS,
    PRICE_ALERT_SWEEP_SECONDS,
    PRICE_HISTORY_SWEEP_SECONDS,
    ROUTE_RUNS_RETENTION_SWEEP_SECONDS,
    PRESENCE_SWEEP_CHECK_SECONDS,
    _broadcast_events,
)
from src.services.scheduler.presence_helpers import (
    _run_due_ticks_sync,
    _repair_orphan_schedules_sync,
    _seed_trader_rosters_sync,
    _bulk_fill_traders_sync,
    _assign_trader_notoriety_sync,
    _assign_trader_missions_sync,
    _relocate_stranded_npcs_sync,
    _disperse_law_patrols_sync,
    _run_retention_sweep_sync,
    _run_citizen_rebake_sweep_sync,
    _run_presence_sweep_sync,
    _run_aria_prune_async,
    _run_route_runs_retention_sync,
)
from src.services.scheduler.economy_governance_sweeps import (
    _run_genesis_completion_sync,
    _run_planetary_advance_sync,
    _run_governance_sweep_sync,
    _run_construction_advance_sync,
    _run_economic_metrics_snapshot_sync,
)
from src.services.scheduler.economy_sweeps import (
    _run_idle_income_sweep_sync,
    _run_daily_stipend_sweep_sync,
    _run_bounty_accrual_sweep_sync,
    _run_port_operating_costs_sync,
    _run_station_recovery_sync,
    _run_reclaim_flag_sweep_sync,
    _run_price_recompute_flush_sync,
    _run_price_alert_sweep_sync,
    _run_price_history_sweep_sync,
)
from src.services.scheduler.reputation_team_sweeps import (
    _run_weekly_decay_sync,
    _run_sustained_reputation_drip_sweep_sync,
    _run_team_reputation_sweep_sync,
)
from src.services.scheduler.pirate_npc_sweeps import (
    _run_suspect_clear_sweep_sync,
    _run_pirate_ecosystem_tick_sync,
)
from src.services.scheduler.contract_sweeps import (
    _run_contract_generation_sync,
    _run_contract_expire_sweep_sync,
)

logger = logging.getLogger(__name__)


async def _contract_generation_loop() -> None:
    """NPC contract generation (WO-ECON-CONTRACT-1-KERNEL), on its OWN
    independent task — WO-SCHED-LOOP-WEDGE.

    Used to run inline inside npc_scheduler_loop's main while-True, ahead
    of expiry/suspect/team-rep/PECO in the same iteration. Generation's
    reachability search scales with station + contract-table size (see
    _all_hop_distances / _load_directed_sector_graph in contract_generator.
    py — WarpTunnel edges connecting every region through the Nexus, added
    616d122, mean the reachable set from any origin can span the whole
    galaxy graph); a slow or first-run-at-real-scale pass took long enough
    that every sweep sequenced after it in that same iteration never ran —
    npc_scheduler_loop has no per-iteration timeout, so this wedged the
    ENTIRE scheduler (confirmed live, heimdall, 2026-07-10: ~17min with
    zero completed main-loop iterations after 0bc6e1f made generation
    reachable every tick instead of once per elapsed-drifted window).

    Isolating it here means however long one pass takes — even hung — the
    main loop's other sweeps are structurally unaffected; they simply stop
    hearing from this task, not from each other. Same durable, wall-clock,
    restart-safe due-check as before (_sweep_due_and_advance via
    _run_contract_generation_sync's own _CONTRACT_GENERATION_STATE_KEY /
    CONTRACT_GENERATION_SWEEP_SECONDS) — decoupling the loop this runs on
    changes nothing about ITS OWN cadence guarantee, only what it can no
    longer block. Cancelled alongside npc_scheduler_loop — see that
    function's own task lifecycle handling.

    Owns the `cancel_event` (WO-SCHED-GEN-ORPHAN-CANCEL) that guards
    against an orphaned worker thread outliving this task after
    cancellation — see _run_contract_generation_sync's own docstring for
    why the thread can't just be stopped directly. Set the INSTANT this
    coroutine observes its own cancellation (the CancelledError handler
    below), while it's still possibly awaiting the very
    asyncio.to_thread() call whose underlying thread needs to see it —
    every phase boundary the sync side checks (peek/gather/compute) still
    has to run before the write phase, so there's ample time for the flag
    to land before that thread would otherwise reach it."""
    cancel_event = threading.Event()
    while True:
        try:
            generated = await asyncio.to_thread(_run_contract_generation_sync, cancel_event)
            if generated:
                logger.info(
                    "NPC scheduler: contract generation — posted %d new contract(s)",
                    generated,
                )
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        except Exception:
            logger.exception("NPC scheduler: contract generation sweep crashed (loop continues)")
        await asyncio.sleep(TICK_SECONDS)


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

    # WO-SCHED-LOOP-WEDGE: contract generation runs on its OWN task
    # (_contract_generation_loop above), never inline in the while-True
    # below — see that function's own docstring for why. Tied to this
    # loop's lifetime: cancelled in the finally below whenever this loop
    # exits, for any reason, so it's never left orphaned.
    contract_gen_task = asyncio.create_task(_contract_generation_loop())
    try:
        await _npc_scheduler_main_loop()
    finally:
        contract_gen_task.cancel()
        try:
            await contract_gen_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "NPC scheduler: contract-generation task raised during shutdown (ignored)"
            )


async def _npc_scheduler_main_loop() -> None:
    """The fast-sweep main loop (WO-SCHED-LOOP-WEDGE split out of
    npc_scheduler_loop) — expiry/suspect/team-rep/PECO and everything else
    that was already here, UNCHANGED. Only contract generation moved off
    this loop; nothing about the rest of the dispatch below changed."""
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
                completed, genesis_events = await asyncio.to_thread(
                    _run_genesis_completion_sync
                )
                if completed:
                    logger.info(
                        "NPC scheduler: completed %d due genesis formation(s)",
                        completed,
                    )
                # WO-G4: broadcast the per-owner genesis_progress frames
                # POST-COMMIT, here on the event loop (never from the worker
                # thread the sweep ran in). Best-effort inside _broadcast_events.
                if genesis_events:
                    await _broadcast_events(genesis_events)
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

        # CRT-4 Lane D: drain + push the staged Research-Directive cockpit frames
        # (contract_offer / contract_settled / rp_governor_status) POST-COMMIT on
        # the event loop. The writer (settle_contracts / faucet sweep, in the
        # worker thread above) stages them into research_service._PENDING_FRAMES;
        # this is the true-push delivery so a frame reaches the owner without
        # waiting for a cockpit GET. Best-effort (mirrors genesis_progress).
        try:
            from src.services import research_service as _rs
            await _rs.broadcast_pending_research_frames()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "NPC scheduler: research cockpit frame broadcast failed (loop continues)"
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
                    gov.get("auto_created")
                    or gov.get("opened")
                    or gov.get("tallied")
                    or gov.get("enacted")
                    or gov.get("rejected")
                ):
                    logger.info(
                        "NPC scheduler: governance sweep — %d auto-created, "
                        "%d opened, %d tallied, %d enacted, %d rejected",
                        gov.get("auto_created", 0), gov.get("opened", 0),
                        gov.get("tallied", 0), gov.get("enacted", 0),
                        gov.get("rejected", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "NPC scheduler: governance sweep crashed (loop continues)"
                )

        # TradeDock shipyard construction-advance sweep (hold-expiry forfeiture
        # + 50% deposit split, queue→slip promotions, phase progression, rent
        # and claim-window expiry). Drives construction_service._advance_station
        # for every station with a live build so the berth pipeline no longer
        # depends on a player synchronously touching the station — runs in the
        # worker thread, own session, own advisory lock. Idempotent + a no-op
        # when nothing is due. Coarse cadence (construction phases are slow);
        # NO-CANON, env-overridable.
        if elapsed % CONSTRUCTION_ADVANCE_CHECK_SECONDS == 0:
            try:
                built = await asyncio.to_thread(_run_construction_advance_sync)
                if built.get("stations"):
                    logger.info(
                        "NPC scheduler: construction advance — %d station "
                        "shipyard pipeline(s) progressed",
                        built.get("stations", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "NPC scheduler: construction advance sweep crashed (loop continues)"
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

        # Economy faucet (WEEKLY) — galactic-citizen subscription perk ONLY.
        # Max's 2026-06-20 split moved the rep stipend to the DAILY sweep below;
        # this weekly path now pays only the paid citizen perk. Same coarse
        # pre-filter / durable-anchor pattern as the weekly decay; intentionally
        # on a separate cadence (20 min) to avoid colliding with the decay wake.
        # run_weekly_faucet_sync is fully synchronous and self-gated on the
        # shared advisory lock.
        if elapsed % FAUCET_CHECK_SECONDS == 0:
            try:
                from src.services.economy_faucet_service import run_weekly_faucet_sync
                faucet = await asyncio.to_thread(run_weekly_faucet_sync)
                if faucet.get("week", -1) >= 0:
                    logger.info(
                        "NPC scheduler: weekly economy faucet fired (week %d) — "
                        "citizen_perk=%d citizen(s), total=%d cr injected",
                        faucet["week"], faucet["citizen_grants"],
                        faucet["total_credits"],
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

        # Idle passive-income faucet — credit each player who owns a
        # passive-income-equipped ship (quantum_harvester) once per UTC day, so
        # the purchased effect is no longer inert (ship-systems.md
        # §passive_income: "applied per-tick by an idle-income job"). Coarse
        # elapsed pre-filter (30 min) so we don't scan equipment_slots every
        # 60s; the once-per-day-per-ship guarantee + restart-proofing come from
        # the durable per-ship UTC-date anchor inside the sweep. Own session,
        # own advisory lock, per-ship failure isolated — same discipline as the
        # genesis/planetary/governance/snapshot sweeps. NO-CANON cadence +
        # magnitude (flagged for the orchestrator).
        if elapsed % IDLE_INCOME_CHECK_SECONDS == 0:
            try:
                idle = await asyncio.to_thread(_run_idle_income_sweep_sync)
                if idle.get("ships"):
                    logger.info(
                        "NPC scheduler: idle-income faucet — credited %d ship(s) "
                        "across %d player(s), %d cr granted",
                        idle.get("ships", 0), idle.get("players", 0),
                        idle.get("credits", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: idle-income faucet crashed (loop continues)")

        # Daily rep-stipend faucet — credit each player who logged in THIS UTC
        # day their per-reputation-tier stipend, once per day (idle day = 0).
        # Max's 2026-06-20 split moved the rep stipend off the weekly faucet onto
        # this DAILY, active-gated cadence. Coarse elapsed pre-filter (35 min) so
        # we don't scan players every 60s; the once-per-day-per-player guarantee
        # + restart-proofing come from the durable per-player UTC-date anchor in
        # Player.settings inside the sweep. Own session, own advisory lock,
        # per-player failure isolated — same discipline as the idle-income /
        # genesis / planetary / governance / snapshot sweeps. NO-CANON cadence +
        # per-tier magnitudes (flagged for the orchestrator).
        if elapsed % DAILY_STIPEND_CHECK_SECONDS == 0:
            try:
                stipend = await asyncio.to_thread(_run_daily_stipend_sweep_sync)
                if stipend.get("players"):
                    logger.info(
                        "NPC scheduler: daily rep-stipend faucet — credited %d "
                        "active player(s), %d cr granted",
                        stipend.get("players", 0), stipend.get("credits", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: daily rep-stipend faucet crashed (loop continues)")

        # System-bounty pot accrual (WO-BN) — grow each criminal's STORED system-
        # bounty pot once per canonical day (base rate scaled by negative-rep
        # severity, capped per tier). The pot is paid-then-reset on a kill+collect
        # in bounty_service; this is its GROWTH engine. Coarse elapsed pre-filter
        # (40 min) so we don't scan players every 60s; the once-per-canonical-day
        # guarantee + restart-proofing come from the durable per-player canonical-
        # day anchor in Player.settings inside the sweep. Own session, own advisory
        # lock, per-criminal failure isolated — same discipline as the idle-income
        # / daily-stipend / genesis / planetary / governance / snapshot sweeps.
        # NO-CANON cadence + accrual rate/multipliers/caps (flagged for the
        # orchestrator).
        if elapsed % BOUNTY_ACCRUAL_CHECK_SECONDS == 0:
            try:
                accrued = await asyncio.to_thread(_run_bounty_accrual_sweep_sync)
                if accrued.get("criminals"):
                    logger.info(
                        "NPC scheduler: system-bounty accrual — grew %d criminal "
                        "pot(s) by %d cr total",
                        accrued.get("criminals", 0), accrued.get("credits", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: system-bounty accrual crashed (loop continues)")

        # Port operating-cost sweep (WO-B3) — charge each player-owned port its
        # accrued maintenance/upkeep and force-sell any port insolvent for the
        # canon 3-month threshold. Drives port_ownership_service.accrue_operating_
        # costs (the lazy, idempotent charge + insolvency engine) on the scheduler
        # so accrual no longer requires a manual /accrue-costs call — an unvisited
        # port now pays upkeep and an abandoned one force-sells autonomously.
        # Coarse elapsed pre-filter (45 min) so we don't scan stations every 60s;
        # the once-per-elapsed-canonical-day guarantee + restart-proofing come from
        # the durable per-station anchor (ownership['costs_accrued_at']) inside the
        # engine — a re-run in the same period computes elapsed_days <= 0 and
        # no-ops (NO double-charge, NO double force-sell). Own session, own advisory
        # lock, per-port failure isolated — same discipline as the bounty-accrual /
        # idle-income / daily-stipend / genesis / planetary / governance sweeps.
        # Maintenance rate + 3-month insolvency threshold are CANON (reused, not
        # reinvented); only the sweep cadence is NO-CANON (flagged for the
        # orchestrator). ECONOMY-SENSITIVE: the insolvency path force-sells.
        if elapsed % PORT_OPERATING_COST_CHECK_SECONDS == 0:
            try:
                ports = await asyncio.to_thread(_run_port_operating_costs_sync)
                if ports.get("ports") or ports.get("insolvent"):
                    logger.info(
                        "NPC scheduler: port operating-cost sweep — charged %d "
                        "port(s); %d force-sold (insolvent)",
                        ports.get("ports", 0), ports.get("insolvent", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: port operating-cost sweep crashed (loop continues)")

        # Station recovery sweep (WO-DBB-EC6) — auto-rebuild any destroyed
        # station whose 24-canonical-hour recovery window has elapsed
        # (FEATURES/economy/trading.md § Destruction & recovery). Drives
        # station_service.recover_station (rebuild commodities to 50% of the
        # destruction-time snapshot, clear the destroyed flag/timer, zero
        # re-purchasable defenses) on the scheduler so a destroyed station
        # rebuilds autonomously without any player visit. Coarse elapsed
        # pre-filter (50 min) so we don't scan stations every 60s; the
        # once-per-window guarantee + restart-proofing come from the durable
        # per-station deadline (recovery_time) + canonical-hours anchor
        # (ownership['destroyed_at']) inside is_recovery_due — a re-run finds
        # is_destroyed False and no-ops (NO double-rebuild). Own session, own
        # advisory lock, per-station failure isolated — same discipline as the
        # port operating-cost / bounty-accrual / idle-income sweeps. The 24h
        # window + 50% rebuild fraction are CANON (reused, not reinvented); only
        # the sweep cadence is NO-CANON.
        if elapsed % STATION_RECOVERY_CHECK_SECONDS == 0:
            try:
                recov = await asyncio.to_thread(_run_station_recovery_sync)
                if recov.get("recovered"):
                    logger.info(
                        "NPC scheduler: station recovery sweep — rebuilt %d "
                        "destroyed station(s) at 50%% inventory",
                        recov.get("recovered", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: station recovery sweep crashed (loop continues)")

        # Inactivity-reclamation flag sweep (PL4b — planet abandonment/reclaim).
        # Stamp planets.reclaimable_at on every owned, non-hub planet whose owner
        # has been inactive for the canon 90 days (and clear the stamp on any
        # planet whose owner has since returned). Drives abandonment_service.
        # flag_inactive_planets — the ADVISORY, idempotent, REVERSIBLE flagger
        # that NEVER deletes a row or reassigns ownership: it only moves the
        # marker. Ownership changes ONLY when a PLAYER fires the reclaim route,
        # and only AFTER the 7-day grace window past the flag (the displaced
        # owner's 7-day tenure floor gates compensation, not the flag). Coarse
        # elapsed pre-filter (55 min) so we don't scan planets every 60s; the
        # once-per-condition guarantee + restart-proofing come from the durable
        # per-planet marker (reclaimable_at) inside the sweep — a re-run
        # re-evaluates the same condition and no-ops for steady-state rows (NO
        # double-flag, and crucially NO auto-reclaim from this sweep). Own
        # session, own advisory lock — same discipline as the station-recovery /
        # port-cost / bounty sweeps. This block is SEPARATE from the CRT-4
        # research-frame drain above and does not touch it.
        if elapsed % RECLAIM_FLAG_CHECK_SECONDS == 0:
            try:
                reclaim = await asyncio.to_thread(_run_reclaim_flag_sweep_sync)
                if reclaim.get("flagged") or reclaim.get("cleared"):
                    logger.info(
                        "NPC scheduler: reclaim-flag sweep — flagged %d "
                        "inactive-owner planet(s), cleared %d returned-owner flag(s)",
                        reclaim.get("flagged", 0), reclaim.get("cleared", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: reclaim-flag sweep crashed (loop continues)")

        # Sustained-reputation-drip sweep (factions-and-teams.md:229-230,
        # WO-PROG-SUSTAINED-DRIPS) — a player sustaining Heroic+ personal
        # reputation (>= +250) for 7+ canonical days drips -5/day Fringe
        # Alliance; a player sustaining Outlaw+ (<= -250) for 7+ canonical
        # days drips -2/day Mercantile Guild. Like the port-cost /
        # station-recovery / reclaim-flag sweeps, the cadence is a COARSE
        # elapsed pre-filter; the once-per-canonical-day-per-player
        # guarantee + restart-proofing come from the durable per-player
        # anchor in Player.settings["sustained_tier"].
        if elapsed % SUSTAINED_REP_DRIP_CHECK_SECONDS == 0:
            try:
                sustained = await asyncio.to_thread(_run_sustained_reputation_drip_sweep_sync)
                if sustained.get("heroic_dripped") or sustained.get("outlaw_dripped"):
                    logger.info(
                        "NPC scheduler: sustained-reputation-drip sweep — "
                        "%d Heroic+ drip(s), %d Outlaw+ drip(s) (of %d "
                        "candidate(s) scanned)",
                        sustained.get("heroic_dripped", 0),
                        sustained.get("outlaw_dripped", 0),
                        sustained.get("players_scanned", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: sustained-reputation-drip sweep crashed (loop continues)")

        # Price-recompute flush sweep (WO-DBB-EC4, ADR-0051 SK30) — settle any
        # station the hot read path deferred (pending_price_recomputation set
        # when a recompute was rate-limited inside the ~1s wall-clock window)
        # so a flagged station does not stay stale. Own session, per-station
        # row-lock + flag re-check inside flush_pending_recomputes, per-station
        # try/except — same isolation discipline as the other sweeps. A ~60s
        # cadence keeps deferred reprices fresh; the flag is durable, so a
        # restart just resumes from whatever is still flagged.
        if elapsed % PRICE_RECOMPUTE_FLUSH_SECONDS == 0:
            try:
                flushed = await asyncio.to_thread(_run_price_recompute_flush_sync)
                if flushed:
                    logger.info(
                        "NPC scheduler: price-recompute flush — repriced %d "
                        "pending station(s)",
                        flushed,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: price-recompute flush sweep crashed (loop continues)")

        # Price-alert sweep — evaluate every active PriceAlert against the
        # current MarketPrice rows so NPC-driven price moves (Loop A trades,
        # flush_pending_recomputes, production restocks) trigger alerts within
        # one cadence, not only when the owning player trades the exact
        # commodity.  Runs at the same default cadence as the price-recompute
        # flush so freshly settled prices are evaluated promptly; per-alert
        # cooldown (DEFAULT_COOLDOWN_SECONDS=300 in price_alert_service) is
        # the fine-grained flap suppressor.
        if elapsed % PRICE_ALERT_SWEEP_SECONDS == 0:
            try:
                alerted = await asyncio.to_thread(_run_price_alert_sweep_sync)
                if alerted:
                    logger.info(
                        "NPC scheduler: price-alert sweep — fired %d alert(s)",
                        alerted,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: price-alert sweep crashed (loop continues)")

        # Price-history snapshot sweep (WO-ECON-MKT-TIMESERIES) — write one
        # hourly PriceHistory row per (station, commodity) from the current
        # MarketPrice row, so market_prediction_engine's PriceHistory-preferred
        # series and economy_analytics' price-trend charts finally have real
        # data (the table had readers but zero writers). Rolls hourly rows
        # into daily and daily into weekly on their calendar boundary, and
        # prunes past the retention window. Own session, own advisory lock,
        # failure isolated — same discipline as the other sweeps.
        if elapsed % PRICE_HISTORY_SWEEP_SECONDS == 0:
            try:
                history = await asyncio.to_thread(_run_price_history_sweep_sync)
                if any(history.values()):
                    logger.info(
                        "NPC scheduler: price-history sweep — %d hourly, "
                        "%d daily, %d weekly row(s) written, %d pruned",
                        history.get("hourly", 0), history.get("daily", 0),
                        history.get("weekly", 0), history.get("pruned", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: price-history sweep crashed (loop continues)")

        # Route-optimization-run retention sweep (WO-OPS-ROUTE-RUNS-RETENTION)
        # — route_optimization_runs is written on every successful player
        # optimize call with no cap; prunes rows that are BOTH older than
        # ROUTE_RUNS_RETENTION_DAYS AND beyond each player's newest
        # ROUTE_RUNS_RETENTION_MAX_PER_PLAYER rows (a low-volume player's
        # stale history and a high-volume player's recent runs both
        # survive). Own session, own advisory lock, failure isolated — same
        # discipline as the other sweeps.
        if elapsed % ROUTE_RUNS_RETENTION_SWEEP_SECONDS == 0:
            try:
                pruned = await asyncio.to_thread(_run_route_runs_retention_sync)
                if pruned.get("deleted"):
                    logger.info(
                        "NPC scheduler: route-run retention sweep — pruned "
                        "%d row(s)",
                        pruned.get("deleted", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: route-run retention sweep crashed (loop continues)")

        # ARIA storage-prune pass (WO-F16) — evict each player's oldest ARIA
        # memory/market-intelligence rows until their combined payload is back
        # under the per-player hard cap (MAX_PLAYER_ARIA_BYTES, 10 MiB). UNLIKE
        # the sweeps above, the prune kernel is ASYNC (it owns its per-player
        # commit on an AsyncSession), so this pass is AWAITED DIRECTLY here on
        # its own async session — NOT run through asyncio.to_thread (a coroutine
        # in a worker thread has no running loop and would never execute). The
        # once-per-canonical-day guarantee comes from a durable Galaxy.state
        # anchor inside _run_aria_prune_async (mirroring the G18 recompute); a
        # coarse elapsed pre-filter keeps us from opening an async session every
        # 60s. Idempotent + a no-op once the day's prune is done; under-cap
        # players are untouched.
        if elapsed % ARIA_PRUNE_CHECK_SECONDS == 0:
            try:
                pruned = await _run_aria_prune_async()
                if pruned.get("players_pruned"):
                    logger.info(
                        "NPC scheduler: ARIA storage prune — pruned %d of %d "
                        "player(s), evicted %d stale row(s)",
                        pruned.get("players_pruned", 0),
                        pruned.get("players_scanned", 0),
                        pruned.get("rows_evicted", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: ARIA storage prune pass crashed (loop continues)")

        # Retention at-risk-signal sweep (WO-RE2) — READ-ONLY compute of the 7
        # canonical at-risk signals (OPERATIONS/retention.md) per active player,
        # flagging anyone who crosses a threshold INTO the re-engagement queue.
        # SYNC signal computer (sync Session), so run via asyncio.to_thread like
        # the weekly-decay / planetary sweeps — NOT awaited inline. The
        # once-per-canonical-day guarantee + the ONLY write (the queue upsert)
        # live inside _run_retention_sweep_sync, which is READ-ONLY on the
        # activity tables and isolates per-player failures with a savepoint. A
        # coarse elapsed pre-filter keeps us off Postgres on idle 60s wakes.
        if elapsed % RETENTION_SWEEP_CHECK_SECONDS == 0:
            try:
                ret = await asyncio.to_thread(_run_retention_sweep_sync)
                if ret.get("day", -1) >= 0:
                    logger.info(
                        "NPC scheduler: retention sweep — flagged %d, "
                        "resolved %d (of %d scanned, canonical day %d)",
                        ret.get("players_flagged", 0),
                        ret.get("rows_resolved", 0),
                        ret.get("players_scanned", 0),
                        ret.get("day", -1),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: retention sweep pass crashed (loop continues)")

        # Citizen-conditional ship RE-BAKE sweep (WO-GC-C leg 4) — THE FIREWALL
        # trigger. Re-bakes every hull carrying a citizen-conditional slot (today
        # the Citizen Clipper's EXTRA slot) through the live resolver so a lapsed
        # Galactic-Citizen's citizen slot goes inert (0 stat; hull persists +
        # flyable; re-subscribe restores) and an active citizen's is restored /
        # left byte-identical (idempotent). SYNC bake path (sync Session), so run
        # via asyncio.to_thread like the retention sweep — NOT awaited inline. The
        # once-per-canonical-day guarantee + the per-ship savepoint isolation live
        # inside _run_citizen_rebake_sweep_sync (distinct advisory lock). A coarse
        # elapsed pre-filter keeps us off Postgres on idle 60s wakes; ≤24h lag is
        # firewall-safe (capped utility, not power/income).
        if elapsed % CITIZEN_REBAKE_CHECK_SECONDS == 0:
            try:
                reb = await asyncio.to_thread(_run_citizen_rebake_sweep_sync)
                if reb.get("day", -1) >= 0:
                    logger.info(
                        "NPC scheduler: citizen re-bake — rebaked %d of %d "
                        "hull(s) (canonical day %d)",
                        reb.get("ships_rebaked", 0),
                        reb.get("ships_scanned", 0),
                        reb.get("day", -1),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: citizen re-bake sweep pass crashed (loop continues)")

        # Stale-presence sweep (WO-PRESWEEP) — drop offline players from
        # Sector.players_present so the who's-here list isn't polluted by players
        # who logged out / went idle (keyed on last_game_login).
        if elapsed % PRESENCE_SWEEP_CHECK_SECONDS == 0:
            try:
                pres = await asyncio.to_thread(_run_presence_sweep_sync)
                if pres.get("presence_entries_swept"):
                    logger.info(
                        "NPC scheduler: presence sweep — removed %d stale entr(y/ies) "
                        "across %d sector(s)",
                        pres.get("presence_entries_swept", 0),
                        pres.get("sectors", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: presence sweep crashed (loop continues)")

        # NPC contract generation (WO-ECON-CONTRACT-1-KERNEL) — WO-SCHED-
        # LOOP-WEDGE: NOT dispatched here anymore. It used to run inline,
        # sequentially before expiry/suspect/team-rep in this SAME
        # iteration — a slow/heavy generation pass (whole-galaxy
        # reachability search, scales with station + contract-table size,
        # see _all_hop_distances) blocked every sweep sequenced after it,
        # wedging the whole loop for good (this loop has no per-iteration
        # timeout). It now runs on its own independent task
        # (_contract_generation_loop, started/cancelled alongside this
        # loop below) so it can never starve anything here again.

        # NPC contract expiry (WO-ECON-CONTRACT-1-KERNEL) — bulk-expires any
        # posted-but-never-accepted contract strictly past its deadline.
        # Tighter cadence than generation — a stale board is more visible
        # to players than a slow generation refresh. WO-SCHED-CADENCE-DRIFT:
        # same no-gate treatment as contract generation above.
        try:
            expired = await asyncio.to_thread(_run_contract_expire_sweep_sync)
            if expired:
                logger.info(
                    "NPC scheduler: contract expiry sweep — expired %d contract(s)",
                    expired,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NPC scheduler: contract expiry sweep crashed (loop continues)")

        # Suspect auto-clear sweep (WO-CMB-SUSPECT-LIFE-1 held wiring) —
        # ships.md:293's "auto-clears at suspect_until" guarantee needed a
        # sweep since nothing else re-checks a stale is_suspect flag once
        # the triggering encounter is over. WO-SCHED-CADENCE-DRIFT: same
        # no-gate treatment — see contract generation's comment above.
        try:
            cleared = await asyncio.to_thread(_run_suspect_clear_sweep_sync)
            if cleared:
                logger.info(
                    "NPC scheduler: suspect auto-clear sweep — cleared %d player(s)",
                    cleared,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NPC scheduler: suspect auto-clear sweep crashed (loop continues)")

        # Team-reputation recalculation sweep (WO-RT-TEAM-REP held wiring)
        # — recalculates every team whose next_recalculation is due.
        # WO-SCHED-CADENCE-DRIFT: same no-gate treatment as above.
        try:
            team_rep = await asyncio.to_thread(_run_team_reputation_sweep_sync)
            if team_rep.get("recalculated"):
                logger.info(
                    "NPC scheduler: team-reputation sweep — recalculated %d of %d "
                    "due team(s)",
                    team_rep.get("recalculated", 0), team_rep.get("due", 0),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NPC scheduler: team-reputation sweep crashed (loop continues)")

        # Pirate-ecosystem weekly growth + evolution tick sweep
        # (WO-PIRATE-ECO-2 held wiring) — the outer per-region/per-holding
        # loop pirate_ecosystem_service's own docstrings named as deferred
        # scheduler work (Lane B). WO-SCHED-CADENCE-DRIFT: unlike its 4
        # siblings above, PECO's real interval is daily (86400s default) —
        # large enough that a coarse elapsed pre-filter (PIRATE_ECOSYSTEM_
        # TICK_CHECK_SECONDS, 55m) is safe (drift on a 55-minute pre-filter
        # is negligible against a day-scale target), matching the citizen-
        # rebake/ARIA-prune/retention convention. The durable wall-clock
        # anchor inside _run_pirate_ecosystem_tick_sync is still what
        # actually guarantees once-per-real-day regardless of restarts.
        if elapsed % PIRATE_ECOSYSTEM_TICK_CHECK_SECONDS == 0:
            try:
                eco = await asyncio.to_thread(_run_pirate_ecosystem_tick_sync)
                if eco.get("growth_actions") or eco.get("evolutions"):
                    logger.info(
                        "NPC scheduler: pirate-ecosystem tick — %d region(s) ticked, "
                        "%d growth action(s), %d holding(s) evaluated, %d evolution(s)",
                        eco.get("regions_ticked", 0), eco.get("growth_actions", 0),
                        eco.get("holdings_evaluated", 0), eco.get("evolutions", 0),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NPC scheduler: pirate-ecosystem tick sweep crashed (loop continues)")
