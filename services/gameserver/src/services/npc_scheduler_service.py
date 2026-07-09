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
import hashlib
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Tuple

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


def _dispatch_governance_medals(db: Session, player_id) -> None:
    """Fire the medals-lane governance hook
    ``medal_service.check_and_award_governance_medals(db, player_id)`` after a
    policy authored by ``player_id`` is enacted (diplomatic.lawgiver /
    ordinances_passed).

    Defensive: resolved by ``getattr`` (the medals lane may be absent),
    idempotent on the medals side, and any failure is logged and swallowed — a
    medal hiccup must NEVER break the governance finalize sweep."""
    try:
        import src.services.medal_service as _medal_module
        hook = getattr(_medal_module, "check_and_award_governance_medals", None)
        if callable(hook):
            hook(db, player_id)
    except Exception as e:  # never let a medal hiccup break the sweep
        logger.error("Governance medal dispatch hook failed: %s", e)


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

# TradeDock SHIPYARD construction-advance sweep cadence. construction_service.
# _advance_station — the whole shipyard berth pipeline (hold-expiry forfeiture
# with the 50% deposit split, queue→slip promotions, phase progression, rent
# and claim-window expiry) — was written as advance-on-READ only: it ran ONLY
# when a player synchronously touched the station via the construction API
# (quote/status/deliver/pay/claim/cancel). A build whose owner stopped logging
# in would freeze mid-pipeline — an expired hold never forfeits its slip, the
# next-in-queue reservation never gets promoted, a finished hull never enters
# its claim window. This sweep makes the canonical clock authoritative for ALL
# stations with a live (non-terminal) reservation, mirroring the planetary /
# governance sweeps. _advance_station is the AUTHORITY on what is due (it gates
# every transition on timers/states), so the sweep merely DRIVES it: it is
# time-accurate (settles exactly the windows that elapsed) and idempotent (a
# caught-up station, or a station already settled by an interleaved API read, is
# a clean no-op) — re-runs are safe. Construction phases are SLOW (canonical
# hours/days), so a coarse cadence is plenty; keyed off durable per-reservation
# timestamps (hold_expires_at / phase_deadline / rent_paid_until /
# claim_expires_at), so a missed boundary on restart is just settled by the next
# sweep (nothing is lost). xact-advisory-lock-gated so a second instance skips
# instead of double-advancing; per-station with_for_update + per-station commit;
# per-station failure isolated so one bad station cannot abort the batch.
#
# CADENCE IS NO-CANON: tradedock-shipyard / ADR-0039 specify the pipeline TIMERS
# but not how often a background worker should DRIVE them. 600s (10 min) is far
# finer than the smallest construction window (the 24 canonical-hour slip hold,
# which at GAME_TIME_SCALE=144 is still 10 wall-clock minutes), so no transition
# visibly lags, while keeping DB churn low. Env-overridable; flagged for the
# orchestrator / a DECISIONS.md ruling.
CONSTRUCTION_ADVANCE_CHECK_SECONDS = int(
    os.environ.get("CONSTRUCTION_ADVANCE_CHECK_SECONDS", str(10 * 60))
)

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
# Galaxy.state JSONB key holding the canonical-DAY index of the last
# Region.active_players_30d recompute (WO-G18). The recompute rides the
# governance sweep (every GOVERNANCE_SWEEP_SECONDS), but the COUNT(DISTINCT)
# aggregate over the 30-day activity window is heavy, so a durable per-day
# anchor — mirroring _WEEKLY_DECAY_STATE_KEY's discipline — gates it to run at
# most ONCE per canonical day (nightly) regardless of process restarts.
_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY = "active_players_30d_last_day"
# Rolling activity window for the Region.active_players_30d metric (canon: a
# player counts as "active in this region" if they logged any PlayerActivity in
# one of the region's sectors within the trailing 30 days).
_ACTIVE_PLAYERS_WINDOW_DAYS = 30
# Galaxy.state JSONB key holding the canonical-DAY index of the last treasury
# reconciliation pass (ADR-0059 N-I4 / WO-REGOV-TREASURY-RECON). The recompute
# rides the governance sweep as Phase 6, gated to run at most ONCE per
# canonical day — mirroring _ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY's discipline —
# regardless of process restarts.
_TREASURY_RECON_STATE_KEY = "treasury_reconciliation_last_day"
# Galaxy.state JSONB key holding the canonical-DAY index of the last ARIA
# storage-prune pass (WO-F16). The dormant prune kernel
# (ARIAPersonalIntelligenceService.prune_player_storage) evicts each player's
# oldest ARIAPersonalMemory + ARIAMarketIntelligence rows until that player's
# combined payload is back under MAX_PLAYER_ARIA_BYTES (10 MiB). The prune is
# ASYNC (it owns its own commit per player against an AsyncSession) so — unlike
# the to_thread sync sweeps — it is awaited DIRECTLY by npc_scheduler_loop on an
# async session; calling it through asyncio.to_thread would run an async coroutine
# in a worker thread with no running loop. A durable per-day anchor — mirroring
# _ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY / _WEEKLY_DECAY_STATE_KEY's discipline —
# gates the (potentially heavy) all-players scan to run at most ONCE per canonical
# day regardless of process restarts.
_ARIA_PRUNE_STATE_KEY = "aria_storage_prune_last_day"
# Coarse CHEAP pre-filter cadence for the ARIA storage-prune pass. The durable
# canonical-day anchor (_ARIA_PRUNE_STATE_KEY) is what actually guarantees
# once-per-day; this only keeps us from opening an async session + querying
# Galaxy.state every 60s. A 45-minute pre-filter is far finer than a (canonical)
# day, so a day's prune is never missed, while idle wakes do nothing. Offset from
# the sync coarse probes (decay 15m / faucet 20m / snapshot 25m / idle 30m /
# stipend 35m / bounty 40m) by landing at 45m so the probes don't all hit
# Postgres on the same scheduler wake.
ARIA_PRUNE_CHECK_SECONDS = int(
    os.environ.get("ARIA_PRUNE_CHECK_SECONDS", str(45 * 60))
)
# Retention at-risk-signal sweep (WO-RE2). Durable canonical-day anchor in
# Galaxy.state gates the all-active-players READ-ONLY signal scan to run at most
# ONCE per canonical day across restarts — mirrors _ARIA_PRUNE_STATE_KEY.
_RETENTION_SWEEP_STATE_KEY = "retention_signal_sweep_last_day"
# Coarse CHEAP pre-filter cadence for the retention sweep. The durable day anchor
# is what guarantees once-per-canonical-day; this just keeps us from opening a
# session + querying Galaxy.state every 60s. Offset to 50m so it doesn't collide
# with the ARIA prune (45m) or the sync coarse probes on the same wake.
RETENTION_SWEEP_CHECK_SECONDS = int(
    os.environ.get("RETENTION_SWEEP_CHECK_SECONDS", str(50 * 60))
)
# Citizen-conditional ship re-bake sweep (WO-GC-C leg 4). Durable canonical-day
# anchor in Galaxy.state gates the re-bake of every hull carrying a
# citizen-conditional slot (today: the Citizen Clipper's EXTRA slot) to run at
# most ONCE per canonical day across restarts — the same once-per-day discipline
# as the retention / ARIA-prune anchors. This is the FIREWALL trigger: re-baking
# through the live resolver makes a lapsed Galactic-Citizen's citizen-conditional
# slot contribute 0 stat (the hull persists + stays flyable; re-subscribe
# restores), while an active citizen's slot is restored / left byte-identical
# (idempotent). A ≤24h re-bake lag is firewall-safe because the perk is capped
# utility, not power/income.
_CITIZEN_REBAKE_STATE_KEY = "citizen_rebake_last_day"
# Coarse CHEAP pre-filter cadence for the citizen re-bake sweep. The durable day
# anchor is what guarantees once-per-canonical-day; this just keeps us from
# opening a session + querying Galaxy.state on every 60s tick. 50m like the
# retention sweep (the day anchor de-dupes a same-day collision to a no-op).
CITIZEN_REBAKE_CHECK_SECONDS = int(
    os.environ.get("CITIZEN_REBAKE_CHECK_SECONDS", str(50 * 60))
)
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

# Idle passive-income faucet pre-filter. The quantum_harvester equipment grants
# {"passive_income": N} (ship_upgrade_service.EQUIPMENT_DEFINITIONS) but, before
# this sweep, NOTHING ever credited it — the purchased effect was inert
# (ship-systems.md §passive_income: "applied per-tick by an idle-income job").
# This sweep credits each player who owns a passive-income-equipped ship once
# per UTC day. Like the economy snapshot, the cadence is a COARSE elapsed
# pre-filter (so we don't take the advisory lock + scan equipment_slots every
# 60s); the once-per-day-per-ship guarantee comes from a DURABLE per-ship anchor
# — the UTC date string stored in the ship's equipment_slots JSONB under the
# reserved _passive_income meta key (additive, NO migration; mirrors the
# cargo['_capacity_bonus_percent'] meta-key convention). A 30-minute pre-filter
# is far finer than a day, so a day's grant is never missed even across restarts
# (the process-relative elapsed counter resets, but the durable per-ship date
# does not — a restart or a re-run within the same UTC day re-reads the anchor
# and skips, so the faucet NEVER double-credits). Offset from the other coarse
# probes (decay 15m / faucet 20m / snapshot 25m) by landing at 30m so the
# four coarse probes don't all hit Postgres on the same scheduler wake.
#
# CADENCE + MAGNITUDE ARE NO-CANON: ship-systems.md §passive_income is 📐
# Design-only — it specifies neither the per-period figure (EQUIPMENT_DEFINITIONS
# carries 100, the only concrete number) nor the period. Daily was chosen to
# match the other daily faucet/snapshot sweeps and to keep the credit faucet
# conservative. Both are flagged for the orchestrator / a DECISIONS.md ruling.
IDLE_INCOME_CHECK_SECONDS = int(
    os.environ.get("IDLE_INCOME_CHECK_SECONDS", str(30 * 60))
)
# Reserved meta key inside Ship.equipment_slots JSONB holding the UTC date
# (YYYY-MM-DD) of the last passive-income grant for that ship — the durable
# per-ship idempotency anchor. Leading underscore namespaces it apart from real
# equipment slot keys (quantum_harvester/mining_laser/…), exactly as
# cargo['_capacity_bonus_percent'] is kept apart from cargo commodity keys.
_PASSIVE_INCOME_ANCHOR_KEY = "_passive_income_last_utc_date"

# Daily rep-stipend faucet pre-filter. Max's 2026-06-20 ruling SPLIT the old
# weekly economy faucet: the galactic-citizen subscription perk stays WEEKLY
# (run_weekly_faucet_sync, above), but the reputation-tier stipend moved to this
# DAILY, ACTIVE-GATED sweep — each player who logged in THAT UTC day receives a
# per-reputation-tier stipend once per day (idle day = 0). Like the idle-income
# faucet, the cadence is a COARSE elapsed pre-filter (so we don't take the
# advisory lock + scan players every 60s); the once-per-day-per-player guarantee
# + restart-proofing come from a DURABLE per-player UTC-date anchor in
# Player.settings JSONB (additive, NO migration; mirrors the per-ship
# _passive_income anchor and the cargo['_capacity_bonus_percent'] convention). A
# 30-minute pre-filter is far finer than a day, so a day's grant is never missed
# even across restarts (the process-relative elapsed counter resets, but the
# durable per-player date does not — a restart or a re-run within the same UTC
# day re-reads the anchor and skips, so the faucet NEVER double-credits). Offset
# from the other coarse probes (decay 15m / faucet 20m / snapshot 25m / idle
# 30m) by landing at 35m so the coarse probes don't all hit Postgres on the same
# scheduler wake.
#
# CADENCE + the per-faction daily MAGNITUDES + the global cap + the good-standing
# threshold are NO-CANON (economy_faucet_service.PER_FACTION_DAILY_BY_LEVEL,
# GLOBAL_DAILY_STIPEND_CAP, and _GOOD_STANDING_MIN_NUMERIC_LEVEL carry the
# figures — summed per good-standing faction, capped under the weekly citizen
# perk) — flagged for the orchestrator / a DECISIONS.md ruling.
DAILY_STIPEND_CHECK_SECONDS = int(
    os.environ.get("DAILY_STIPEND_CHECK_SECONDS", str(35 * 60))
)

# System-bounty pot accrual pre-filter (WO-BN). The SYSTEM bounty is now a STORED
# pot per criminal (Player.settings JSONB) that GROWS over time and RESETS to 0
# on a kill+collect. This sweep is the GROWTH engine: once per canonical DAY it
# bumps every criminal's pot by a per-tier daily accrual (BountyService.
# accrue_system_bounty_pot — base rate scaled by negative-rep severity, capped at
# the tier figure). Like the idle-income / daily-stipend faucets, the cadence is
# a COARSE elapsed pre-filter (so we don't take the advisory lock + scan players
# every 60s); the once-per-canonical-day-per-criminal guarantee + restart-proofing
# come from a DURABLE per-player anchor — the canonical-day index stored in
# Player.settings[system_bounty_pot_period] inside accrue_system_bounty_pot. The
# accrual keys off the CANONICAL day (canonical_day_number) so it is observable on
# dev (GAME_TIME_SCALE=144 → a canonical day elapses every ~10 wall-clock min) and
# self-consistent with the scheduler's canonical clock; a restart or re-run within
# the same canonical day re-reads the anchor and skips, so the pot NEVER double-
# accrues. Additive JSONB only; NO migration, NO new table. Offset from the other
# coarse probes (decay 15m / faucet 20m / snapshot 25m / idle 30m / stipend 35m)
# by landing at 40m so the coarse probes don't all hit Postgres on the same wake.
#
# CADENCE + the base accrual rate + per-tier dastardly multipliers + the per-tier
# caps are NO-CANON (bounties.md gives the tier FIGURES but is silent on growth);
# the figures live in bounty_service (SYSTEM_BOUNTY_BASE_ACCRUAL_PER_DAY,
# SYSTEM_BOUNTY_ACCRUAL_MULTIPLIER, SYSTEM_BOUNTY_TIERS) — flagged for a
# DECISIONS.md ruling. ECONOMY-SENSITIVE: the pot is a credit faucet (paid on
# kill), so the idempotency anchor and the per-tier caps are load-bearing.
BOUNTY_ACCRUAL_CHECK_SECONDS = int(
    os.environ.get("BOUNTY_ACCRUAL_CHECK_SECONDS", str(40 * 60))
)

# Port operating-cost sweep pre-filter (WO-B3). The maintenance/upkeep accrual
# + 3-month insolvency force-sell live in port_ownership_service.accrue_operating_
# costs — a LAZY, idempotent engine that, before this sweep, only fired via the
# manual POST /stations/{id}/accrue-costs endpoint. An unvisited port therefore
# NEVER paid its upkeep and an abandoned port NEVER force-sold. This sweep DRIVES
# that existing engine on the scheduler so accrual + insolvency happen
# autonomously. Like the bounty/idle/stipend sweeps, the cadence is a COARSE
# elapsed pre-filter (so we don't take the advisory lock + scan stations every
# 60s); the once-per-elapsed-canonical-day guarantee + restart-proofing come from
# the DURABLE per-station anchor already written by accrue_operating_costs —
# ownership['costs_accrued_at'] (an existing JSONB key on Station.ownership). A
# restart or a re-run within the same canonical day re-reads the anchor, computes
# elapsed_days <= 0, and no-ops — so the charge NEVER double-debits and the
# insolvency clock NEVER double-counts (no double force-sell). Additive JSONB
# only; NO migration, NO new column. Offset from the other coarse probes (decay
# 15m / faucet 20m / snapshot 25m / idle 30m / stipend 35m / bounty 40m) by
# landing at 45m so the coarse probes don't all hit Postgres on the same wake.
#
# CADENCE IS NO-CANON: port-ownership canon specifies the maintenance rate (1%
# acquisition/month, in maintenance_for_days) and the 3-month insolvency
# threshold (INSOLVENCY_MONTHS) — BOTH reused here, NOT reinvented. Only the
# background SWEEP cadence (45m pre-filter, once-per-canonical-day granularity via
# the anchor) is an implementation choice — flagged for a DECISIONS.md ruling.
# ECONOMY-SENSITIVE: the insolvency force-sell clears ownership + re-lists at a
# depreciated price, so the per-station anchor idempotency is load-bearing.
PORT_OPERATING_COST_CHECK_SECONDS = int(
    os.environ.get("PORT_OPERATING_COST_CHECK_SECONDS", str(45 * 60))
)

# Station-recovery sweep cadence (WO-DBB-EC6). Rebuild any station whose
# 24-CANONICAL-hour destroyed-recovery window has elapsed (FEATURES/economy/
# trading.md § Destruction & recovery). Like the port-operating-cost / bounty /
# idle-income / stipend sweeps, the cadence is a COARSE elapsed pre-filter (so
# we don't take the advisory lock + scan stations every 60s); the actual
# per-station eligibility comes from the DURABLE deadline (Station.recovery_time)
# + the canonical-hours cross-check against ownership['destroyed_at'] inside
# station_service.is_recovery_due, both of which survive a restart (the
# process-relative elapsed counter resets; the stored timestamps do not). A
# re-run finds is_destroyed False and no-ops — NO double-rebuild. The 24h window
# + 50% rebuild fraction are CANON (reused from station_service, NOT reinvented);
# only this background SWEEP cadence is NO-CANON. Offset to 50m so it does not
# share a wake with the other coarse probes (decay 15m / faucet 20m / snapshot
# 25m / idle 30m / stipend 35m / bounty 40m / port-costs 45m). On dev
# (GAME_TIME_SCALE=144) a 24-canonical-hour window elapses in 10 wall-clock
# minutes, so a coarse cadence keeps the rebuild within ~one sweep of due.
STATION_RECOVERY_CHECK_SECONDS = int(
    os.environ.get("STATION_RECOVERY_CHECK_SECONDS", str(50 * 60))
)

# Inactivity-reclamation flag sweep cadence (PL4b — planet abandonment/reclaim).
# Stamps planets.reclaimable_at on every owned, non-hub planet whose owner's
# Player.last_game_login is older than the canon INACTIVITY_DAYS=90 (and clears
# the stamp on any planet whose owner is no longer stale). Like the port-cost /
# bounty / station-recovery sweeps, the cadence is a COARSE wall-clock pre-filter
# (so we don't take the advisory lock + scan planets every 60s); the actual
# eligibility comes from the DURABLE per-planet marker (planets.reclaimable_at)
# cross-checked against the owner's last_game_login inside abandonment_service.
# flag_inactive_planets, which survives a restart (the process-relative elapsed
# counter resets; the stored stamps do not). The flag is ADVISORY + REVERSIBLE —
# the sweep NEVER deletes a row or reassigns ownership; a re-run in the same
# period re-evaluates the same condition and is a no-op for steady-state rows.
# Offset to 55m so it does not share a wake with the other coarse probes
# (decay 15m / faucet 20m / snapshot 25m / idle 30m / stipend 35m / bounty 40m /
# port-costs 45m / station-recovery 50m). The 90-day inactivity / 7-day grace /
# 7-day tenure numbers are Max-APPROVED (PL4b); only this background sweep
# cadence is operational.
RECLAIM_FLAG_CHECK_SECONDS = int(
    os.environ.get("RECLAIM_FLAG_CHECK_SECONDS", str(55 * 60))
)

# Price-recompute flush cadence (WO-DBB-EC4, ADR-0051 SK30). The hot
# market-read path debounces full price recomputes to once per ~1 wall-clock
# second per station (TradingService.maybe_recompute_price); a suppressed
# recompute flags Station.pending_price_recomputation. This sweep DRIVES
# TradingService.flush_pending_recomputes to settle those deferred reprices so
# a flagged station does not stay stale. The durable per-station state is the
# pending_price_recomputation flag (survives a restart; the process-relative
# elapsed counter does not — a re-run just finds the flag cleared and no-ops).
# A ~60s cadence keeps deferred reprices fresh without churn; it is fine for
# this to coincide with the tick wake (the sweep is cheap and idempotent).
PRICE_RECOMPUTE_FLUSH_SECONDS = int(
    os.environ.get("PRICE_RECOMPUTE_FLUSH_SECONDS", str(60))
)
# Price-alert sweep cadence — evaluate every active PriceAlert against the
# current MarketPrice rows so NPC-driven price moves (Loop A trades,
# flush_pending_recomputes, production restocks) trigger alerts within one
# cadence, not only on a player's own trade. Runs at the same default cadence
# as the price-recompute flush so freshly settled prices are evaluated promptly;
# per-alert cooldown_seconds (DEFAULT_COOLDOWN_SECONDS=300 in
# price_alert_service) is the fine-grained flap suppressor.
PRICE_ALERT_SWEEP_SECONDS = int(
    os.environ.get("PRICE_ALERT_SWEEP_SECONDS", str(60))
)

# Price-history snapshot sweep cadence (WO-ECON-MKT-TIMESERIES). The
# price_history table had readers (market_prediction_engine's preferred
# series, economy_analytics' _get_price_trends) but ZERO writers — every
# prediction/chart ran on an empty table. This sweep writes one hourly
# PriceHistory row per (station, commodity) from the current MarketPrice
# row, rolling hourly rows into daily and daily rows into weekly snapshots
# on their respective calendar boundaries. Hourly cadence matches the
# canon snapshot granularity directly (DATA_MODELS/economy.md:122-130), so
# no coarse pre-filter is needed the way the daily/weekly sweeps use one.
PRICE_HISTORY_SWEEP_SECONDS = int(
    os.environ.get("PRICE_HISTORY_SWEEP_SECONDS", str(60 * 60))
)
# Retention/pruning window (NO-CANON — canon names the hourly/daily/weekly
# snapshot cadences but is silent on how long each is kept; proposed to
# DECISIONS: 7 days of hourly rows, 90 days of daily rows, weekly rows kept
# indefinitely as the long-horizon trend series).
PRICE_HISTORY_HOURLY_RETENTION_DAYS = int(
    os.environ.get("PRICE_HISTORY_HOURLY_RETENTION_DAYS", "7")
)
PRICE_HISTORY_DAILY_RETENTION_DAYS = int(
    os.environ.get("PRICE_HISTORY_DAILY_RETENTION_DAYS", "90")
)
# Trend-glyph epsilon used by the trading UI to decide up/down/flat
# (NO-CANON — proposed alongside the retention window). Exposed here so the
# one magic number lives next to its sibling economy constants rather than
# being re-declared in the frontend with no cross-reference.
PRICE_TREND_EPSILON = float(os.environ.get("PRICE_TREND_EPSILON", "0.005"))

# Route-optimization-run retention sweep (WO-OPS-ROUTE-RUNS-RETENTION).
# route_optimization_runs (written by route_optimizer.py / ai.py's
# _record_optimization_run on every successful player optimize call) is
# append-only telemetry for the NH18 admin feed with no cap and no prune
# job — the authoring spec (WO-SB-RO2) deliberately deferred this: "a prune
# job is out of scope — flag retention policy to DECISIONS". NO-CANON: canon
# is silent on both numbers below; proposed to DECISIONS — keep 30 days of
# history, and never more than 200 rows per player regardless of age (so a
# low-volume player's full history survives, a high-volume/spammy player's
# ancient runs don't pile up unbounded).
ROUTE_RUNS_RETENTION_DAYS = int(
    os.environ.get("ROUTE_RUNS_RETENTION_DAYS", "30")
)
ROUTE_RUNS_RETENTION_MAX_PER_PLAYER = int(
    os.environ.get("ROUTE_RUNS_RETENTION_MAX_PER_PLAYER", "200")
)
# Sweep cadence — daily is enough for a telemetry-retention job (it is not a
# player-facing signal); env-overridable like every other sweep cadence.
ROUTE_RUNS_RETENTION_SWEEP_SECONDS = int(
    os.environ.get("ROUTE_RUNS_RETENTION_SWEEP_SECONDS", str(24 * 60 * 60))
)

# Session-level advisory lock key (pg_try_advisory_xact_lock argument).
_ADVISORY_LOCK_KEY = 0x53573231  # 'SW21'

# DISTINCT advisory-lock key for the citizen-conditional ship re-bake sweep
# (WO-GC-C leg 4). Intentionally NOT the global _ADVISORY_LOCK_KEY: the re-bake
# sweep mutates ship rows + stat columns and must serialize only against another
# concurrent re-bake pass, not against the many unrelated sweeps that share the
# global key — a distinct key lets it run without blocking (or being blocked by)
# the retention / faucet / construction sweeps on the same wake. 'GCRB' =
# Galactic-Citizen Re-Bake.
_CITIZEN_REBAKE_LOCK_KEY = 0x47435242  # 'GCRB'

# DISTINCT advisory-lock key for the stale-presence sweep (WO-PRESWEEP). Idempotent
# (re-removing an absent player is a no-op), so it serializes only vs another
# presence pass. 'PRSW'.
_PRESENCE_SWEEP_LOCK_KEY = 0x50525357
# Cadence + staleness window for the presence sweep. Coarse — a lingering who's-here
# entry is cosmetic, not urgent. STALE window is NO-CANON (no canon presence-TTL).
PRESENCE_SWEEP_CHECK_SECONDS = 5 * 60
PRESENCE_STALE_MINUTES = 30

# Mask to a signed-63-bit non-negative range. pg_try_advisory_xact_lock(bigint)
# takes a signed 64-bit key; staying inside the low 63 bits keeps the value
# non-negative so a stable hash never overflows or collides with Postgres'
# two-int32 lock-key overload.
_LOCK_KEY_MASK_63 = (1 << 63) - 1


def region_lock_key(region_id: Any) -> int:
    """Derive a STABLE, deterministic per-region advisory-lock key from the
    global base key and the region id, so disjoint regions can serialize
    independently (a future regionalized sweep acquires
    ``pg_try_advisory_xact_lock(region_lock_key(region_id))`` per region while
    still serializing every worker WITHIN that region).

    Determinism is load-bearing: every gameserver instance must compute the
    SAME key for the SAME region_id, so this uses a content hash
    (``hashlib.blake2b``) — NOT Python's built-in ``hash()``, whose str/bytes
    hashing is per-process randomized (``PYTHONHASHSEED``) and would give two
    instances DIFFERENT keys for the same region (defeating the lock). The
    region id is XOR-folded against ``_ADVISORY_LOCK_KEY`` so per-region keys
    stay in the same family as the global key, and the result is masked to a
    non-negative signed-63-bit int safe for the Postgres ``bigint`` argument.

    ``region_id`` of None falls back to the global ``_ADVISORY_LOCK_KEY`` so a
    region-agnostic caller degenerates to the single global lock (the same
    serialization the whole scheduler uses today)."""
    if region_id is None:
        return _ADVISORY_LOCK_KEY
    digest = hashlib.blake2b(str(region_id).encode("utf-8"), digest_size=8).digest()
    region_hash = int.from_bytes(digest, "big")
    return (_ADVISORY_LOCK_KEY ^ region_hash) & _LOCK_KEY_MASK_63

# ADR-0063: recruit lifecycle stage lasts 7 canonical days, then ACTIVE.
RECRUIT_STAGE_HOURS = 7 * 24

# npc-lifecycle.md career-stage canon (lines 139-140, 148-149):
#   senior at >= 90 canonical-days tenure, combat +5% / scanner +1;
#   decorated on faction-medal earn, buff scales with medal count.
SENIOR_TENURE_HOURS = 90 * 24            # canon: "Tenure >= 90 real-time days"
SENIOR_COMBAT_BONUS = 0.05               # canon: "combat +5%"
SENIOR_SCANNER_BONUS = 1                 # canon: "scanner range +1"
# Decorated buff "scales with medal count" (canon, npc-lifecycle.md:140,149).
# Doc gives no per-medal magnitude, so each medal adds the same step the
# senior tier grants — a conservative, canon-anchored scaling unit rather
# than an invented number. NO-CANON: the per-medal step size.
DECORATED_COMBAT_PER_MEDAL = SENIOR_COMBAT_BONUS   # NO-CANON
DECORATED_SCANNER_PER_MEDAL = SENIOR_SCANNER_BONUS  # NO-CANON

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

def _run_genesis_completion_sync() -> Tuple[int, List[Dict[str, Any]]]:
    """Complete forming genesis planets whose timer has elapsed.

    Before this tick, formation completion settled ONLY lazily — GenesisService.
    complete_due_formations runs on a player's owned-planets fetch and is scoped
    to that one player. A colony whose owner never re-checks the Colonial
    Registry (or an abandoned/unowned forming planet) would therefore stay
    "forming" forever past its 48h timer. This periodic sweep makes the timer
    authoritative for everyone. Cheap (an indexed forming/past-due filter that
    returns nothing on a steady galaxy), idempotent, xact-advisory-lock-gated
    so a second instance skips instead of double-completing.

    WO-G4: returns ``(completed_count, events)`` where ``events`` is a list of
    best-effort ``genesis_progress`` frames — one per OWNED planet that just
    advanced to complete — collected via the GenesisService out-param. Like
    every tick-body event, the caller broadcasts these POST-COMMIT on the EVENT
    LOOP (``_broadcast_events``), NOT from this worker thread (no running loop
    here — a worker-thread→loop bridge is forbidden). The frames are composed
    inside complete_all_due_formations BEFORE its internal commit and handed
    back only after it returns (i.e. after that commit succeeded), so a WS
    hiccup can never roll back a completion."""
    from src.core.database import SessionLocal
    from src.services.genesis_service import GenesisService

    db = SessionLocal()
    events: List[Dict[str, Any]] = []
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0, events
        # GenesisService.complete_all_due_formations commits internally when it
        # completes any planet; that commit also releases this xact lock. The
        # genesis_progress frames are appended to ``events`` (out-param) as each
        # owned planet completes, returned post-commit for loop-side broadcast.
        completed = GenesisService(db).complete_all_due_formations(events=events)
        if not completed:
            db.commit()  # release the lock on the no-op path
        return completed, events
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
    from sqlalchemy import or_, and_
    from src.services.structures import settle

    result = {"terraforming": 0, "siege": 0, "production": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # ONE unioned candidate set (CRT WO-K1a §5.3): terraforming_active OR (under_siege AND
        # siege_started_at) OR (owner_id AND colonists>0). Each planet is visited ONCE through
        # structures.settle() — the single planetary tick that advances terraform + (held) siege
        # morale + commodity production and drains the research faucet (now step 5 of settle(),
        # re-homed from the prior chained sweep_research_faucet call), each on its OWN inner anchor
        # in its OWN clock domain. This collapses the prior three filtered phase-loops and
        # eliminates the double-visit when a planet sat in two phase-sets. Per-planet
        # commit/rollback discipline is preserved (one bad planet rolls back only itself); settle()
        # leaves the commit to the caller and self-no-ops every step that doesn't apply, so this
        # stays a cheap no-op on a steady galaxy. NOTE: siege LIFECYCLE (_detect_siege) is
        # intentionally NOT run here — the sweep has no owner/enemy context; settle() only ADVANCES
        # a held siege's morale, exactly as the prior advance_siege phase did (neither started nor
        # lifted sieges).
        candidates = (
            db.query(Planet.id)
            .filter(
                or_(
                    Planet.terraforming_active.is_(True),
                    and_(
                        Planet.under_siege.is_(True),
                        Planet.siege_started_at.isnot(None),
                    ),
                    and_(
                        Planet.owner_id.isnot(None),
                        Planet.colonists > 0,
                    ),
                )
            )
            .all()
        )
        for (planet_id,) in candidates:
            try:
                planet = (
                    db.query(Planet)
                    .filter(Planet.id == planet_id)
                    .with_for_update()
                    .first()
                )
                if planet is None:
                    continue
                res = settle(planet, db=db)
                if res.changed:
                    db.commit()
                    if "terraform" in res.steps_changed:
                        result["terraforming"] += 1
                    if "siege" in res.steps_changed:
                        result["siege"] += 1
                    if "production" in res.steps_changed or "research" in res.steps_changed:
                        result["production"] += 1
                else:
                    db.rollback()  # release the row lock; nothing changed
            except Exception:
                logger.exception(
                    "Planetary advance (settle) failed for planet %s", planet_id,
                )
                db.rollback()

        # Release the advisory lock held on this session's transaction. Each per-planet commit
        # above already released it once; a final commit closes out any open transaction (e.g. the
        # rollback after the last no-op planet) so the lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Planetary advance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Treasury reconciliation — ADR-0059 N-I4 / WO-REGOV-TREASURY-RECON
# ---------------------------------------------------------------------------

def reconcile_region_treasuries(db: Session) -> Dict[str, int]:
    """Verify SUM(RegionalTreasuryEntry.delta) == Region.treasury_balance for
    every ACTIVE region — the exact invariant RegionalTreasuryEntry's own
    docstring (region.py) names as this table's purpose.

    Two bounded queries, NOT one per region: a single grouped SUM aggregate
    across every region's ledger rows, then a single filtered fetch of every
    ACTIVE region's id + treasury_balance. A region with zero ledger entries
    never appears in the grouped aggregate's result set at all (nothing to
    group), so its ledger sum is read as the Python default 0 rather than a
    SQL NULL — comparing cleanly against treasury_balance without a crash or
    a COALESCE.

    ALERT-ONLY: a mismatch is logged via ``logger.error`` naming the region
    and both figures. This function NEVER writes to treasury_balance or the
    ledger — it is a verification pass, not a repair pass. (NO-CANON: no
    ops-alert bus exists yet; ``logger.error`` is the interim channel pending
    a DECISIONS ruling on an admin-facing notification surface.)

    Returns {"checked": <active regions examined>, "mismatched": <count>}.
    """
    from src.models.region import Region, RegionStatus, RegionalTreasuryEntry
    from sqlalchemy import func as sa_func

    ledger_sums = dict(
        db.query(
            RegionalTreasuryEntry.region_id,
            sa_func.sum(RegionalTreasuryEntry.delta),
        )
        .group_by(RegionalTreasuryEntry.region_id)
        .all()
    )

    active_regions = (
        db.query(Region.id, Region.treasury_balance)
        .filter(Region.status == RegionStatus.ACTIVE)
        .all()
    )

    mismatched = 0
    for region_id, balance in active_regions:
        ledger_sum = int(ledger_sums.get(region_id, 0) or 0)
        balance = int(balance or 0)
        if ledger_sum != balance:
            mismatched += 1
            logger.error(
                "Treasury reconciliation MISMATCH region_id=%s ledger_sum=%d "
                "treasury_balance=%d drift=%d",
                region_id, ledger_sum, balance, balance - ledger_sum,
            )
    return {"checked": len(active_regions), "mismatched": mismatched}


def _run_treasury_reconciliation_gated(db: Session) -> Dict[str, Any]:
    """Day-gate wrapper around ``reconcile_region_treasuries`` — takes an
    already-open session so it is independently testable (fake session, no
    live DB) without spinning up the whole governance sweep. Mirrors
    ``_run_governance_sweep_sync`` Phase 4's Galaxy.state day-anchor
    discipline EXACTLY, including reading the canonical day via the SAME
    no-arg ``canonical_day_number()`` call (real aware ``datetime.now(UTC)``,
    never the sweep's naive ``now`` — see Phase 4's own comment on why). The
    caller (Phase 6 of the governance sweep) owns the commit/rollback around
    this call, same as every other phase in that sweep.

    Returns {"treasury_checked", "treasury_mismatched", "treasury_recon_skipped"}.
    """
    from src.models.galaxy import Galaxy

    result: Dict[str, Any] = {
        "treasury_checked": 0, "treasury_mismatched": 0, "treasury_recon_skipped": False,
    }

    this_day = canonical_day_number()
    galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
    gstate = dict(galaxy.state or {}) if galaxy is not None else {}
    last_day = gstate.get(_TREASURY_RECON_STATE_KEY)
    already_today = (
        galaxy is not None
        and last_day is not None
        and int(last_day) >= this_day
    )
    if already_today:
        result["treasury_recon_skipped"] = True
        return result

    stats = reconcile_region_treasuries(db)
    result["treasury_checked"] = stats["checked"]
    result["treasury_mismatched"] = stats["mismatched"]

    if galaxy is not None:
        gstate = dict(galaxy.state or {})
        gstate[_TREASURY_RECON_STATE_KEY] = this_day
        galaxy.state = gstate
        flag_modified(galaxy, "state")
    return result


# ---------------------------------------------------------------------------
# Regional governance sweep — open/close elections + finalize policies
# ---------------------------------------------------------------------------

def _run_governance_sweep_sync() -> Dict[str, int]:
    """Drive the regional democratic loop forward on the canonical clock.

    Idempotent phases mirroring the planetary advance sweep's discipline (own
    session, xact advisory lock, per-row with_for_update + per-row commit,
    per-row failure isolation):

      0. AUTO-CREATE due recurring elections: for every active region whose last
         RECURRING_ELECTION_POSITION (governor) election ENDED >= the region's
         election_frequency_days ago (or that has never held one, gauged from
         region.created_at), and that has no in-flight (PENDING/ACTIVE) governor
         election, open the NEXT one in the SCHEDULED phase (status PENDING with
         voting_opens_at = now + lead, voting_closes_at = opens + 7d). This is
         the entry edge of the state machine (canon "Election scheduling") —
         citizens then self-nominate during the SCHEDULED window before Phase 1
         flips it ACTIVE and locks the candidate list.
      1. OPEN due elections: PENDING elections whose voting_opens_at has passed
         become ACTIVE (so voting can begin) — this IS the SCHEDULED -> ACTIVE
         transition that locks the candidate list.
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

    Phase 4 additionally recomputes Region.active_players_30d (WO-G18) —
    self-gated to once per canonical day by a durable Galaxy.state anchor — so
    the region dashboard's activity figure is no longer permanently zero.

    Phase 5 EXPIRES stale treaties (WO-TREATY): any 'active' treaty past its
    expires_at is flipped to 'expired' here on the sweep — GALAXY-WIDE, not
    scoped to a region — so a treaty in an UNOPENED region (whose owner never
    issues a GET /my-region/treaties) still expires. Previously the ONLY thing
    that flipped a stale treaty was RegionalGovernanceService._expire_stale_treaties,
    invoked lazily on read; an unread region's treaties therefore never expired.
    The flip uses the SAME 'active' -> 'expired' literals as the lazy path, so a
    treaty caught by either path is byte-identical.

    Phase 6 reconciles regional treasuries (ADR-0059 N-I4 / WO-REGOV-TREASURY-
    RECON): verifies SUM(RegionalTreasuryEntry.delta) == Region.treasury_balance
    for every ACTIVE region via ``_run_treasury_reconciliation_gated`` /
    ``reconcile_region_treasuries``, self-gated to once per canonical day by a
    durable Galaxy.state anchor (mirroring Phase 4's discipline exactly).
    ALERT-ONLY — a mismatch is logged, never auto-corrected; this phase writes
    nothing to any balance.

    Returns {auto_created, opened, tallied, enacted, rejected,
    regions_recomputed, treaties_expired, treasury_checked,
    treasury_mismatched}.
    """
    from src.core.database import SessionLocal
    from src.models.region import (
        Region, RegionStatus, RegionalElection, RegionalPolicy, RegionalVote,
        RegionalPolicyVote, RegionalTreasuryEntry, RegionalTreaty,
        RegionalMembership, ElectionStatus, PolicyStatus,
    )
    from src.models.planet import Planet, player_planets
    from src.models.sector import Sector
    from src.models.galaxy import Galaxy
    from src.models.player_analytics import PlayerActivity
    from src.services.regional_governance_service import (
        compute_quorum, quorum_pct_for_region, threshold_for_policy,
        determine_election_winner, enact_changes_onto_region,
        compute_treasury_adjustment,
        ELECTION_TALLYING, POLICY_VOTERS_KEY,
        RECURRING_ELECTION_POSITION, ELECTION_VOTING_WINDOW_DAYS,
        ELECTION_SCHEDULED_LEAD_DAYS,
    )
    from sqlalchemy import func as sa_func, update
    from sqlalchemy.orm.attributes import flag_modified

    result = {"auto_created": 0, "opened": 0, "tallied": 0, "enacted": 0,
              "rejected": 0, "regions_recomputed": 0, "treaties_expired": 0,
              "treasury_checked": 0, "treasury_mismatched": 0}
    now = datetime.utcnow()

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # --- Phase 0: auto-create due recurring elections --------------------
        # Canon "Election scheduling": for every active region whose previous
        # governor election ended >= election_frequency_days ago (gauged from
        # region.created_at if it has never held one) and that has no in-flight
        # (PENDING/ACTIVE) governor election, open the NEXT governor election in
        # the SCHEDULED phase (PENDING). voting_opens_at is set a lead-window in
        # the future so citizens can self-nominate before Phase 1 flips it ACTIVE
        # and locks the candidate list. Reproduce-exactly: a manually-created
        # election (born ACTIVE for the same position) registers as in-flight, so
        # the auto-scheduler never duplicates it.
        recurring_regions = (
            db.query(Region.id, Region.election_frequency_days, Region.created_at)
            .filter(Region.status == RegionStatus.ACTIVE)
            .all()
        )
        for (rid, freq_days, created_at) in recurring_regions:
            try:
                # An in-flight (PENDING or ACTIVE) governor election blocks a new
                # one — at most one live election per position (canon step 3).
                in_flight = (
                    db.query(RegionalElection.id)
                    .filter(
                        RegionalElection.region_id == rid,
                        RegionalElection.position == RECURRING_ELECTION_POSITION,
                        RegionalElection.status.in_(
                            [ElectionStatus.PENDING, ElectionStatus.ACTIVE]
                        ),
                    )
                    .first()
                )
                if in_flight is not None:
                    continue

                # The most recent terminal governor election's close time anchors
                # the cadence; with none on record, fall back to region birth so a
                # never-elected region opens its first election once it is old
                # enough.
                last_close = (
                    db.query(sa_func.max(RegionalElection.voting_closes_at))
                    .filter(
                        RegionalElection.region_id == rid,
                        RegionalElection.position == RECURRING_ELECTION_POSITION,
                    )
                    .scalar()
                )
                anchor = last_close or created_at
                if anchor is None:
                    continue
                freq = int(freq_days or 90)
                if (now - anchor) < timedelta(days=freq):
                    continue

                voting_opens_at = now + timedelta(days=ELECTION_SCHEDULED_LEAD_DAYS)
                voting_closes_at = voting_opens_at + timedelta(
                    days=ELECTION_VOTING_WINDOW_DAYS
                )
                new_election = RegionalElection(
                    region_id=rid,
                    position=RECURRING_ELECTION_POSITION,
                    candidates=[],
                    voting_opens_at=voting_opens_at,
                    voting_closes_at=voting_closes_at,
                    status=ElectionStatus.PENDING,
                )
                db.add(new_election)
                db.commit()
                result["auto_created"] += 1
            except Exception:
                logger.exception(
                    "Governance sweep: auto-create failed for region %s", rid
                )
                db.rollback()

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

                # Eligible-voter roll (quorum denominator), colony-aware per WO-CF
                # PATH A — mirrors the async _count_eligible_voters: a player is
                # eligible if they have a can_vote membership row OR own ≥1 colony
                # in the region (resolved through the planet's SECTOR, since
                # Planet.region_id is unreliable). Counted as DISTINCT players so a
                # colony owner with an eligible membership row is not double-counted.
                eligible_member_ids = {
                    pid for (pid,) in db.query(RegionalMembership.player_id)
                    .filter(
                        RegionalMembership.region_id == region.id,
                        RegionalMembership.membership_type.in_(["citizen", "resident"]),
                        RegionalMembership.voting_power > 0,
                    )
                    .all()
                }
                colony_owner_ids = {
                    pid for (pid,) in db.query(player_planets.c.player_id)
                    .select_from(Planet)
                    .join(Sector, Planet.sector_uuid == Sector.id)
                    .join(player_planets, Planet.id == player_planets.c.planet_id)
                    .filter(Sector.region_id == region.id)
                    .distinct()
                    .all()
                }
                eligible = len(eligible_member_ids | colony_owner_ids)
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
                    # Medal: diplomatic.lawgiver (ordinances_passed >= 1) — awarded
                    # to the policy AUTHOR (proposed_by) on genuine enactment, in
                    # this same per-policy transaction (before the commit below);
                    # idempotent on the medals side. Defensive — never breaks the
                    # governance sweep.
                    _dispatch_governance_medals(db, policy.proposed_by)
                    db.commit()
                    result["enacted"] += 1
                else:
                    policy.status = PolicyStatus.REJECTED
                    db.commit()
                    result["rejected"] += 1
            except Exception:
                logger.exception("Governance sweep: finalize failed for policy %s", pid)
                db.rollback()

        # --- Phase 4: recompute Region.active_players_30d (WO-G18) ------------
        # Region.active_players_30d was always 0 (nothing ever wrote it), so the
        # region dashboard's activity figure was dead. Recompute it here, gated
        # to once per canonical DAY by a durable Galaxy.state anchor (the
        # COUNT(DISTINCT) aggregate over a 30-day window is too heavy to run on
        # every 5-minute governance sweep). A player counts as "active in a
        # region" if they logged any PlayerActivity in one of that region's
        # SECTORS within the trailing 30 days — the activity's recorded
        # sector_id (the GLOBAL human-readable Sector.sector_id integer, NOT the
        # Sector.id UUID) resolves to the region it happened in, the same
        # sector→region path the quorum roll above uses because object-level
        # region_id is unreliable. Per-region write + per-region commit with
        # per-region failure isolation, mirroring the sweep's discipline above;
        # always defensive, never fatal to the governance sweep.
        try:
            # No-arg → canonical_day_number defaults to an aware datetime.now(UTC);
            # passing the sweep's naive datetime.utcnow() would make .timestamp()
            # interpret it as LOCAL time and shift the day anchor. Mirrors
            # _run_weekly_decay_sync's this_week = canonical_week_number().
            this_day = canonical_day_number()
            galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
            gstate = dict(galaxy.state or {}) if galaxy is not None else {}
            last_day = gstate.get(_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY)
            already_today = (
                galaxy is not None
                and last_day is not None
                and int(last_day) >= this_day
            )
            if not already_today:
                window_start = now - timedelta(days=_ACTIVE_PLAYERS_WINDOW_DAYS)
                # DISTINCT-player count per region in one grouped aggregate:
                #   COUNT(DISTINCT player_id) of activities in the last 30 days,
                #   joined activity.sector_id (global int) -> Sector.sector_id
                #   -> Sector.region_id.
                counts = dict(
                    db.query(
                        Sector.region_id,
                        sa_func.count(sa_func.distinct(PlayerActivity.player_id)),
                    )
                    .select_from(PlayerActivity)
                    .join(Sector, PlayerActivity.sector_id == Sector.sector_id)
                    .filter(
                        PlayerActivity.timestamp >= window_start,
                        Sector.region_id.isnot(None),
                    )
                    .group_by(Sector.region_id)
                    .all()
                )
                # Iterate ALL regions (not just those with activity) so a region
                # that went quiet is reset to 0 rather than left stale. Per-row
                # commit + per-row isolation: one region's error never aborts the
                # rest.
                region_ids = [rid for (rid,) in db.query(Region.id).all()]
                for rid in region_ids:
                    try:
                        new_count = int(counts.get(rid, 0))
                        updated = (
                            db.query(Region)
                            .filter(Region.id == rid)
                            .update(
                                {Region.active_players_30d: new_count},
                                synchronize_session=False,
                            )
                        )
                        db.commit()
                        if updated:
                            result["regions_recomputed"] += 1
                    except Exception:
                        logger.exception(
                            "Governance sweep: active_players_30d recompute "
                            "failed for region %s", rid,
                        )
                        db.rollback()
                # Advance the durable per-day anchor (best-effort; a failure here
                # just means a harmless re-run next sweep — the recompute is
                # idempotent).
                if galaxy is not None:
                    try:
                        gstate = dict(galaxy.state or {})
                        gstate[_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY] = this_day
                        galaxy.state = gstate
                        flag_modified(galaxy, "state")
                        db.commit()
                    except Exception:
                        logger.exception(
                            "Governance sweep: active_players_30d day-anchor "
                            "advance failed (recompute will re-run next sweep)"
                        )
                        db.rollback()
        except Exception:
            # The recompute must NEVER break the governance sweep proper.
            logger.exception(
                "Governance sweep: active_players_30d recompute phase failed"
            )
            db.rollback()

        # --- Phase 5: expire stale treaties (WO-TREATY) ----------------------
        # GALAXY-WIDE expiry of every 'active' treaty past its expires_at, so a
        # treaty in an UNOPENED region (whose owner never reads it) still
        # expires. Mirrors RegionalGovernanceService._expire_stale_treaties's
        # 'active' -> 'expired' transition but is NOT region-scoped — the lazy
        # read path only ever touched the region being read. Idempotent (a clean
        # no-op once nothing is past its expiry); a failure here must NEVER break
        # the governance sweep proper.
        try:
            expired_result = db.execute(
                update(RegionalTreaty)
                .where(
                    RegionalTreaty.status == "active",
                    RegionalTreaty.expires_at.isnot(None),
                    RegionalTreaty.expires_at < now,
                )
                .values(status="expired")
            )
            expired_count = expired_result.rowcount or 0
            if expired_count:
                db.commit()
                result["treaties_expired"] += expired_count
                logger.info(
                    "Governance sweep: expired %d stale treaty(ies)",
                    expired_count,
                )
            else:
                # Nothing flipped — settle the no-op statement so the advisory
                # lock is not held on an idle transaction.
                db.commit()
        except Exception:
            logger.exception("Governance sweep: treaty expiry phase failed")
            db.rollback()

        # --- Phase 6: treasury reconciliation (WO-REGOV-TREASURY-RECON) ------
        # RegionalTreasuryEntry's own docstring (region.py) names this
        # verification as the ledger's purpose: SUM(delta) must equal
        # Region.treasury_balance for every ACTIVE region. Self-gated to once
        # per canonical day (see _run_treasury_reconciliation_gated), mirroring
        # Phase 4's day-anchor discipline exactly. ALERT-ONLY — a mismatch is
        # logged via logger.error naming the region and both figures; this
        # phase NEVER writes to any balance. A failure here must NEVER break
        # the governance sweep proper.
        try:
            recon = _run_treasury_reconciliation_gated(db)
            result["treasury_checked"] = recon["treasury_checked"]
            result["treasury_mismatched"] = recon["treasury_mismatched"]
            db.commit()
        except Exception:
            logger.exception("Governance sweep: treasury reconciliation phase failed")
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
# TradeDock SHIPYARD construction-advance sweep — drive the berth pipeline
# ---------------------------------------------------------------------------

def _run_construction_advance_sync() -> Dict[str, int]:
    """Drive the TradeDock shipyard construction pipeline forward on the
    canonical clock for every station with a live build.

    Before this sweep, construction_service._advance_station — hold-expiry
    forfeiture (with the 50% deposit split to the next-in-queue reservation),
    queue→slip promotions, build phase progression, slip-rent forfeiture, and
    claim-window expiry — ran ONLY when a player synchronously touched the
    station through the construction API (quote/status/deliver/pay/claim/cancel,
    all of which lazily settle the station first). A build whose owner stopped
    logging in simply froze: an expired hold never released its slip, the next
    reservation in the queue never got promoted, a finished hull never entered
    (or aged out of) its 7-day claim window. This sweep makes the canonical
    clock authoritative for ALL stations with a non-terminal reservation,
    mirroring the planetary / governance sweeps' discipline.

    _advance_station is the AUTHORITY on what is due — it gates every transition
    on the durable per-reservation timestamps/states (hold_expires_at,
    phase_deadline, rent_paid_until, claim_expires_at). The sweep merely DRIVES
    it, so it is time-accurate (settles exactly the windows that elapsed since
    each durable anchor) and idempotent: a caught-up station — or one already
    settled by an interleaved API read between sweeps — is a clean no-op, so a
    re-run (including after a restart) never double-applies a forfeiture or
    double-promotes a slip.

    Candidate set: DISTINCT station ids that have a NON-TERMINAL
    ConstructionReservation (state NOT IN claimed/cancelled/forfeited). A station
    with nothing but terminal rows is skipped without a lock — we never scan
    every station. xact-advisory-lock-gated so a second instance skips instead
    of double-advancing. Per station: with_for_update lock on the Station row
    (the per-station serialization point _advance_station expects its caller to
    hold), then _advance_station + a per-station commit; per-station try/except
    so one bad station cannot abort the batch.

    Returns {stations} — the count of stations whose pipeline actually advanced
    (a transition was logged); a no-op station does not increment it.
    """
    from src.core.database import SessionLocal
    from src.models.construction import ConstructionReservation
    from src.models.station import Station
    from src.services import construction_service

    result = {"stations": 0}
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # Distinct stations carrying at least one live (non-terminal) build.
        # Querying the indexed reservation set — not every station — keeps the
        # sweep cheap on a steady galaxy with few in-flight builds.
        station_rows = (
            db.query(ConstructionReservation.station_id)
            .filter(
                ConstructionReservation.state.notin_(
                    list(construction_service.TERMINAL_STATES)
                )
            )
            .distinct()
            .all()
        )

        for (station_id,) in station_rows:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None:
                    db.rollback()  # release any open txn; station gone
                    continue
                # _advance_station settles the whole pipeline under the held
                # station lock and flushes; it leaves the commit to the caller.
                # We always commit (it may have advanced phases, granted holds,
                # or surfaced rent markers without logging a discrete event), but
                # only count stations that actually transitioned a reservation.
                snapshot = _construction_state_snapshot(db, station_id)
                construction_service._advance_station(db, station, now)
                changed = _construction_state_snapshot(db, station_id) != snapshot
                db.commit()
                if changed:
                    result["stations"] += 1
            except Exception:
                logger.exception(
                    "Construction advance: pipeline failed for station %s",
                    station_id,
                )
                db.rollback()

        # Final commit closes out any open (no-op) transaction so the advisory
        # lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Construction advance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _construction_state_snapshot(db: Session, station_id) -> tuple:
    """A cheap (reservation_id, state) fingerprint of a station's non-terminal
    builds, used only to detect whether _advance_station moved anything this
    pass (so the sweep's count reflects real transitions, not no-op passes).
    Read-only; ordered for stable comparison."""
    from src.models.construction import ConstructionReservation

    rows = (
        db.query(ConstructionReservation.id, ConstructionReservation.state)
        .filter(
            ConstructionReservation.station_id == station_id,
            ConstructionReservation.state.notin_(
                ["claimed", "cancelled", "forfeited"]
            ),
        )
        .order_by(ConstructionReservation.id)
        .all()
    )
    return tuple((str(rid), state) for rid, state in rows)


# ---------------------------------------------------------------------------
# Economy-metrics snapshot — daily galaxy-wide economic state writer
# ---------------------------------------------------------------------------

def _compute_daily_economic_enrichment(
    db: Session,
    *,
    window_start: datetime,
    credit_velocity: float,
) -> Dict[str, Any]:
    """Populate the ~13 EconomicMetrics columns the daily snapshot wrote as
    bare column defaults (DATA_MODELS/economy.md:134-140) -- inflation,
    health, volatility, commodity/sector/station leaders, wealth disparity,
    and new-trader count. Pure/session-injectable so it's testable DB-free,
    mirroring reconcile_region_treasuries's pure-fn/day-gate-wrapper split
    (this function has no day-gate of its own -- the caller already owns
    that via the EconomicMetrics.date uniqueness check).

    DEGRADATION -- each field group has its own try/except: a single
    calculator raising leaves ONLY the fields it feeds at the
    EconomicMetrics column default (logged at warning, never the caller's
    problem, never aborts the snapshot). Defaults below are copy-exact from
    the model (market_transaction.py).

    BOUNDEDNESS -- every query here is a single aggregate/fetch round trip,
    never a per-player or per-transaction Python loop:
      - richest_player_credits: one Player fetch (row count = active
        players, not transactions).
      - most/least traded commodity, most_active_sector,
        most_valuable_station, new_traders: one GROUP BY over the trailing
        24h MarketTransaction window each (row count = distinct
        commodities/sectors/stations/traders that day, not raw rows).
      - commodity_price_index / average_profit_margin: ONE shared,
        unfiltered MarketPrice fetch -- bounded by station x commodity
        cardinality (not by trade volume), the same table the pre-existing
        _calculate_market_liquidity / _get_average_prices calculators
        already scan per-commodity.
    economic_health_score / inflation_rate / market_volatility /
    economic_disparity_index / median_player_credits reuse the EXISTING
    EconomyAnalyticsService calculators (_calculate_inflation_rates,
    _calculate_price_volatility, _calculate_wealth_distribution,
    _calculate_health_score) rather than re-deriving them -- their own
    query cost is that service's existing, already-relied-upon behavior.

    NO-CANON: commodity_price_index's "base period" is canon-silent (the
    model defaults to 100.0 with no documented reference point). This uses
    COMMODITY_BASE_PRICES (the static canonical price table) as the
    denominator -- current buy price vs. canonical base price, summed
    across every live MarketPrice row (each station-commodity row counted
    once, so coverage naturally weights toward commodities carried at more
    stations). Flagged for DECISIONS.md: a rolling prior-snapshot baseline
    is an equally valid reading and would produce a different index
    trajectory over time.
    """
    from sqlalchemy import func as sa_func
    from src.core.commodity_economy import COMMODITY_BASE_PRICES
    from src.models.market_transaction import MarketPrice, MarketTransaction
    from src.services.economy_analytics_service import EconomyAnalyticsService

    fields: Dict[str, Any] = {
        "inflation_rate": 0.0,
        "economic_health_score": 0.5,
        "market_volatility": 0.0,
        "most_traded_commodity": None,
        "least_traded_commodity": None,
        "commodity_price_index": 100.0,
        "most_active_sector": None,
        "most_valuable_station": None,
        "economic_disparity_index": 0.0,
        "richest_player_credits": 0,
        "median_player_credits": 0,
        "new_traders": 0,
        "average_profit_margin": 0.0,
    }

    analytics = EconomyAnalyticsService(db)
    volatility_by_commodity: Dict[str, float] = {}
    wealth_dist: Dict[str, Any] = {}

    try:
        inflation_by_commodity = analytics._calculate_inflation_rates()
        if inflation_by_commodity:
            fields["inflation_rate"] = sum(inflation_by_commodity.values()) / len(inflation_by_commodity)
    except Exception:
        logger.warning("Economy snapshot enrichment: inflation_rate failed, left at default", exc_info=True)

    try:
        volatility_by_commodity = analytics._calculate_price_volatility()
        if volatility_by_commodity:
            fields["market_volatility"] = sum(volatility_by_commodity.values()) / len(volatility_by_commodity)
    except Exception:
        volatility_by_commodity = {}
        logger.warning("Economy snapshot enrichment: market_volatility failed, left at default", exc_info=True)

    try:
        wealth_dist = analytics._calculate_wealth_distribution()
        fields["economic_disparity_index"] = float(wealth_dist.get("gini_coefficient", 0.0))
        fields["median_player_credits"] = int(wealth_dist.get("median_wealth", 0))
    except Exception:
        wealth_dist = {}
        logger.warning("Economy snapshot enrichment: wealth distribution failed, left at default", exc_info=True)

    try:
        # _calculate_health_score returns a 0-100 scale; the column is
        # documented 0-1 (market_transaction.py:205, DATA_MODELS/economy.md).
        raw_score = analytics._calculate_health_score(
            {"price_volatility": volatility_by_commodity}, credit_velocity, wealth_dist,
        )
        fields["economic_health_score"] = raw_score / 100.0
    except Exception:
        logger.warning("Economy snapshot enrichment: economic_health_score failed, left at default", exc_info=True)

    try:
        active_credits = (
            db.query(Player.credits)
            .filter(Player.is_active.is_(True))
            .all()
        )
        if active_credits:
            fields["richest_player_credits"] = max(c for (c,) in active_credits)
    except Exception:
        logger.warning("Economy snapshot enrichment: richest_player_credits failed, left at default", exc_info=True)

    try:
        commodity_rows = (
            db.query(MarketTransaction.commodity, sa_func.sum(MarketTransaction.quantity))
            .filter(MarketTransaction.timestamp >= window_start)
            .group_by(MarketTransaction.commodity)
            .order_by(sa_func.sum(MarketTransaction.quantity).desc())
            .all()
        )
        if commodity_rows:
            fields["most_traded_commodity"] = commodity_rows[0][0]
            fields["least_traded_commodity"] = commodity_rows[-1][0]
    except Exception:
        logger.warning("Economy snapshot enrichment: most/least traded commodity failed, left at default", exc_info=True)

    try:
        sector_row = (
            db.query(MarketTransaction.sector_id, sa_func.count(MarketTransaction.id))
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.sector_id.isnot(None),
            )
            .group_by(MarketTransaction.sector_id)
            .order_by(sa_func.count(MarketTransaction.id).desc())
            .first()
        )
        if sector_row:
            fields["most_active_sector"] = int(sector_row[0])
    except Exception:
        logger.warning("Economy snapshot enrichment: most_active_sector failed, left at default", exc_info=True)

    try:
        station_row = (
            db.query(MarketTransaction.station_id, sa_func.sum(MarketTransaction.total_value))
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.station_id.isnot(None),
            )
            .group_by(MarketTransaction.station_id)
            .order_by(sa_func.sum(MarketTransaction.total_value).desc())
            .first()
        )
        if station_row:
            fields["most_valuable_station"] = station_row[0]
    except Exception:
        logger.warning("Economy snapshot enrichment: most_valuable_station failed, left at default", exc_info=True)

    try:
        # A "new trader" is a player whose EARLIEST-ever transaction falls
        # inside this window -- GROUP BY + HAVING MIN(timestamp), not a
        # window-only COUNT, so a long-time trader who simply traded today
        # doesn't get miscounted as new.
        new_trader_rows = (
            db.query(MarketTransaction.player_id)
            .filter(MarketTransaction.player_id.isnot(None))
            .group_by(MarketTransaction.player_id)
            .having(sa_func.min(MarketTransaction.timestamp) >= window_start)
            .all()
        )
        fields["new_traders"] = len(new_trader_rows)
    except Exception:
        logger.warning("Economy snapshot enrichment: new_traders failed, left at default", exc_info=True)

    try:
        price_rows = db.query(MarketPrice.commodity, MarketPrice.buy_price, MarketPrice.sell_price).all()
        index_numerator = index_denominator = 0.0
        margin_values: List[float] = []
        for commodity, buy_price, sell_price in price_rows:
            base = COMMODITY_BASE_PRICES.get(commodity, {}).get("base")
            if base:
                index_numerator += float(buy_price)
                index_denominator += float(base)
            if sell_price and sell_price > 0:
                margin_values.append((sell_price - buy_price) / sell_price * 100.0)
        if index_denominator > 0:
            fields["commodity_price_index"] = (index_numerator / index_denominator) * 100.0
        if margin_values:
            fields["average_profit_margin"] = sum(margin_values) / len(margin_values)
    except Exception:
        logger.warning(
            "Economy snapshot enrichment: commodity_price_index/average_profit_margin failed, left at default",
            exc_info=True,
        )

    return fields


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

        # Enrichment (WO-ECON-METRICS-ENRICH) -- inflation/health/volatility/
        # commodity+sector+station leaders/wealth disparity/new-traders. Its
        # own internal try/except degrades any single failed calculator to
        # that field's column default; it never raises, so it can't abort
        # this snapshot.
        enrichment = _compute_daily_economic_enrichment(
            db, window_start=window_start, credit_velocity=credit_velocity,
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
            **enrichment,
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
# Idle passive-income faucet — daily credit grant for harvester-equipped ships
# ---------------------------------------------------------------------------

def _run_idle_income_sweep_sync() -> Dict[str, int]:
    """Credit each ship's owner the passive_income its installed equipment grants,
    once per UTC day.

    The quantum_harvester equipment carries {"passive_income": 100}
    (ship_upgrade_service.EQUIPMENT_DEFINITIONS), but nothing ever credited it —
    the purchased effect was inert. ship-systems.md §passive_income: "applied
    per-tick by an idle-income job (periodic credit grant scheduler)". This is
    that job.

    DISCIPLINE — mirrors the genesis/planetary/governance/snapshot sweeps:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-crediting (and the lock auto-releases on commit/rollback);
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent install/uninstall/equip mutation can't race the credit;
      * per-row commit and per-row try/except — one bad ship cannot abort the
        batch or roll back already-credited ships.

    IDEMPOTENCY ACROSS RESTARTS (this is a CREDIT FAUCET, so this is mandatory):
    the durable per-ship anchor is the UTC date string under
    Ship.equipment_slots[_PASSIVE_INCOME_ANCHOR_KEY]. A ship is credited only
    when that anchor is BEHIND today's UTC date; the anchor is then advanced to
    today in the SAME per-row transaction as the credit. A restart, a duplicate
    wake, or a re-run within the same UTC day re-reads the anchor and skips —
    NEVER a double-credit. Additive JSONB only; NO migration, NO new table.

    ELIGIBILITY — a real player's living ship that carries passive_income:
    owner_id NOT NULL (player-owned), is_npc False, is_destroyed False, and
    ShipUpgradeService.get_passive_income(ship) > 0 (the authoritative amount,
    read from EQUIPMENT_DEFINITIONS and summed across multiple sources). The
    owning player's row is locked (with_for_update) before its credits are
    mutated, ordered ship-row-then-player-row to match the upgrade-service lock
    discipline (player before ship there; here the ship is the candidate driver,
    so we lock the ship first, then its owner — both ships and players, so no
    cross-sweep lock-order conflict with the single-row sweeps above).

    CADENCE + MAGNITUDE ARE NO-CANON (ship-systems.md §passive_income is
    📐 Design-only) — daily + the EQUIPMENT_DEFINITIONS figure of 100; flagged
    for a DECISIONS.md ruling.

    Returns {"ships": n_credited, "players": n_distinct_players, "credits":
    total_cr_granted}; all zero on the lock-held / nothing-due no-op paths.
    """
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.models.ship import Ship
    from src.services.ship_upgrade_service import ShipUpgradeService

    result = {"ships": 0, "players": 0, "credits": 0}
    credited_players: set = set()

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work: each
        # per-row commit below would otherwise release the xact lock on the
        # first commit anyway, so we keep the lock only long enough to claim the
        # sweep, then proceed (a second instance that lost the race has already
        # returned above). Commit here so the lock isn't pinned to a long-open
        # transaction while we iterate candidates.
        db.commit()

        # Durable per-ship anchor key: today's UTC date string. A ship whose
        # stored anchor already equals (or is ahead of) today is skipped.
        today = datetime.now(UTC).date()
        today_str = today.isoformat()

        # Candidate ships: player-owned, living, real (non-NPC). We cannot
        # cheaply filter "carries passive_income" in SQL (it lives in the
        # equipment_slots JSONB as an effect of an arbitrary equipment key), so
        # the candidate set is intentionally broad and get_passive_income()
        # below is the authoritative gate. equipment_slots is non-empty for any
        # ship that could qualify, so we prefilter on that to keep the candidate
        # list small on a galaxy of mostly-unequipped hulls.
        candidate_ids = (
            db.query(Ship.id)
            .filter(
                Ship.owner_id.isnot(None),
                Ship.is_npc.is_(False),
                Ship.is_destroyed.is_(False),
                Ship.equipment_slots != text("'{}'::jsonb"),
            )
            .all()
        )

        for (ship_id,) in candidate_ids:
            try:
                ship = (
                    db.query(Ship)
                    .filter(Ship.id == ship_id)
                    .with_for_update()
                    .first()
                )
                if ship is None or ship.is_destroyed or ship.owner_id is None:
                    db.rollback()  # release row lock; nothing to do
                    continue

                amount = ShipUpgradeService.get_passive_income(ship)
                if amount <= 0:
                    db.rollback()
                    continue

                # Durable idempotency gate: only credit when the per-ship anchor
                # is behind today's UTC date. A restart/re-run within the same
                # day re-reads this and skips — never a double-credit.
                slots = ship.equipment_slots if isinstance(ship.equipment_slots, dict) else {}
                last_str = slots.get(_PASSIVE_INCOME_ANCHOR_KEY)
                if isinstance(last_str, str) and last_str >= today_str:
                    # ISO date strings compare lexicographically == chronologically.
                    db.rollback()
                    continue

                # Lock the owning player row before mutating its credits, so a
                # concurrent purchase/grant can't lose this increment.
                player = (
                    db.query(Player)
                    .filter(Player.id == ship.owner_id)
                    .with_for_update()
                    .first()
                )
                if player is None:
                    db.rollback()  # orphaned owner_id — skip
                    continue

                player.credits = int(player.credits or 0) + amount

                # Advance the durable per-ship anchor in the SAME transaction as
                # the credit, so the grant and the idempotency mark commit (or
                # roll back) atomically.
                new_slots = dict(slots)
                new_slots[_PASSIVE_INCOME_ANCHOR_KEY] = today_str
                ship.equipment_slots = new_slots
                flag_modified(ship, "equipment_slots")

                db.commit()
                result["ships"] += 1
                result["credits"] += amount
                credited_players.add(str(player.id))
            except Exception:
                logger.exception(
                    "Idle-income sweep: grant failed for ship %s", ship_id
                )
                db.rollback()

        result["players"] = len(credited_players)
        if result["ships"]:
            logger.info(
                "Idle-income sweep: %s — credited %d ship(s) across %d player(s), "
                "%d cr granted",
                today_str, result["ships"], result["players"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Idle-income sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_daily_stipend_sweep_sync() -> Dict[str, int]:
    """Credit each ACTIVE-TODAY player their PER-FACTION guild stipend, once per
    UTC day (Max's final per-faction ruling 2026-06-20).

    The reputation stipend used to ride the weekly economy faucet (paid to every
    active player on the citizen-perk cadence). It is now DAILY and gated on the
    player having actually logged in THAT UTC day — engagement is rewarded, an
    idle day pays 0. The amount is the SUM of each good-standing faction's
    level-scaled contribution (economy_faucet_service.PER_FACTION_DAILY_BY_LEVEL),
    clamped to GLOBAL_DAILY_STIPEND_CAP so a multi-faction-favored player can
    never out-earn the paid weekly citizen perk. The per-faction reputations are
    read by apply_daily_rep_stipend_for_player through the player's OWN (locked)
    session — this sweep opens no extra session for them.

    DISCIPLINE — mirrors _run_idle_income_sweep_sync EXACTLY:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-crediting (the lock auto-releases on the first commit), then
        commit immediately to claim the sweep without pinning the lock;
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent login/credit mutation can't race the grant;
      * per-row commit and per-row try/except — one bad player cannot abort the
        batch or roll back already-credited players.

    ACTIVE-THAT-DAY GATE: a player is a candidate iff their User.last_login's
    UTC date == today's UTC date. This is a WALL-CLOCK UTC comparison (not the
    canonical clock) so it matches the durable UTC-date anchor and the everyday
    meaning of "logged in today". Idle players are simply not candidates → 0.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — this is a CREDIT FAUCET): the
    durable per-player anchor is the UTC date string under
    Player.settings[_daily_stipend_last_utc_date], advanced in the SAME per-row
    transaction as the credit by apply_daily_rep_stipend_for_player. A player is
    credited only when that anchor is BEHIND today; a restart, duplicate wake, or
    re-run within the same UTC day re-reads it and skips — NEVER a double-credit.
    Additive JSONB only; NO migration, NO new table. A player with NO good-
    standing faction is still anchored for today (0 credits) so subsequent
    same-day wakes short-circuit cheaply.

    CADENCE + per-faction MAGNITUDES + global cap + good-standing threshold are
    NO-CANON (PER_FACTION_DAILY_BY_LEVEL, GLOBAL_DAILY_STIPEND_CAP,
    _GOOD_STANDING_MIN_NUMERIC_LEVEL) — flagged for a DECISIONS.md ruling.

    Returns {"players": n_credited, "credits": total_cr_granted}; all zero on the
    lock-held / nothing-due no-op paths. ``players`` counts rows that received a
    NONZERO grant (a 0-tier player anchored-but-unpaid is not counted)."""
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.models.user import User
    from src.services.economy_faucet_service import (
        apply_daily_rep_stipend_for_player,
    )

    result = {"players": 0, "credits": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the idle-income sweep): claim the sweep, then iterate.
        db.commit()

        today = datetime.now(UTC).date()
        today_str = today.isoformat()

        # Candidate players: active accounts whose USER logged in today (UTC).
        # The durable login timestamp is User.last_login, written on every login
        # by user_service.update_user_last_login / authenticate_player (auth
        # flow). NOTE: Player has no last_login column (it was renamed to
        # last_game_login), and last_game_login is NOT written by the live auth
        # path (track_login is called without a db arg) — so User.last_login is
        # the only reliable active-that-day signal. Filter on the
        # [today 00:00 UTC, tomorrow 00:00 UTC) half-open window in SQL (join
        # Player->User) so the gate is cheap and idle players never enter the set.
        day_start = datetime(today.year, today.month, today.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        candidate_ids = (
            db.query(Player.id)
            .join(User, User.id == Player.user_id)
            .filter(
                Player.is_active.is_(True),
                User.last_login.isnot(None),
                User.last_login >= day_start,
                User.last_login < day_end,
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

                # Re-confirm the active-that-day gate on the locked row via the
                # player's User.last_login (a concurrent re-login could have
                # moved it since the candidate query; the gate must hold on the
                # row we credit). player.user lazy-loads the User (read-only).
                last_login = player.user.last_login if player.user else None
                if last_login is None or not (day_start <= last_login < day_end):
                    db.rollback()
                    continue

                granted = apply_daily_rep_stipend_for_player(player, today_str)

                db.commit()  # grant + anchor advance commit atomically
                if granted:
                    result["players"] += 1
                    result["credits"] += granted
            except Exception:
                logger.exception(
                    "Daily-stipend sweep: grant failed for player %s", player_id
                )
                db.rollback()

        if result["players"]:
            logger.info(
                "Daily-stipend sweep: %s — credited %d active player(s), "
                "%d cr granted",
                today_str, result["players"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Daily-stipend sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_bounty_accrual_sweep_sync() -> Dict[str, int]:
    """Grow every criminal's STORED system-bounty pot once per canonical day
    (WO-BN). This is the GROWTH half of the stored-pot model — bounty_service's
    collect_bounty / collect_bounty_share are the PAYOUT-then-RESET half.

    A criminal (personal_reputation <= the shallowest tier threshold, -500 — the
    SAME proxy the old on-demand model used to decide WHO carried a bounty) has
    its pot bumped by a per-tier daily accrual (base rate × the deeper-pit
    dastardly multiplier), capped at the deepest-matched tier figure. The actual
    accrual + idempotency live in BountyService.accrue_system_bounty_pot; this
    sweep is the scheduler shell that DRIVES it under the standard discipline.

    DISCIPLINE — mirrors _run_idle_income_sweep_sync / _run_daily_stipend_sweep_
    sync EXACTLY:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-accruing (the lock auto-releases on the first commit), then
        commit immediately to claim the sweep without pinning the lock;
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent kill (collect_bounty zeroes the pot under the SAME row lock) or
        a reputation change can't race the accrual;
      * per-row commit and per-row try/except — one bad criminal cannot abort the
        batch or roll back already-accrued criminals.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — the pot is a CREDIT FAUCET): the
    durable per-player anchor is the canonical-day index under
    Player.settings[system_bounty_pot_period], advanced in the SAME per-row
    transaction as the accrual by accrue_system_bounty_pot. A criminal accrues at
    most ONE period's worth per call and only when the anchor is BEHIND today's
    canonical day; a restart, duplicate wake, or re-run within the same canonical
    day re-reads it and skips — NEVER a double-accrual. Additive JSONB only; NO
    migration, NO new table.

    CONCURRENCY vs PAYOUT (anti-faucet): the accrual locks the criminal's Player
    row (with_for_update) before reading+writing the pot, exactly as collect_
    bounty locks it before zeroing. So an accrual and a kill can never interleave
    on the same criminal — whichever takes the row lock first runs to its commit
    first; the other sees the post-state. An accrual that lands just after a kill
    re-grows the pot from 0 (correct: the bounty re-accrues over time).

    CANDIDATE GATE: only criminals are candidates — personal_reputation <=
    SYSTEM_BOUNTY_CRIMINAL_THRESHOLD AND is_active. A non-criminal never enters
    the set (so its pot stays 0 and its anchor is never written). The per-row
    re-read re-confirms criminal status on the locked row (a concurrent rep
    recovery could have lifted them out of criminal range since the candidate
    query — accrue_system_bounty_pot adds 0 in that case but still advances the
    anchor so the period isn't re-evaluated).

    Returns {"criminals": n_accrued, "credits": total_cr_added}; all zero on the
    lock-held / nothing-due no-op paths. ``criminals`` counts rows that received a
    NONZERO accrual (a criminal already at cap, anchored-but-unbumped, is not
    counted)."""
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.services.bounty_service import (
        BountyService,
        SYSTEM_BOUNTY_CRIMINAL_THRESHOLD,
    )

    result = {"criminals": 0, "credits": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the idle-income / stipend sweeps): claim the sweep, then
        # iterate.
        db.commit()

        # Canonical-day index drives both the per-period accrual and the durable
        # idempotency anchor — self-consistent with the scheduler's canonical
        # clock and observable on dev.
        period = canonical_day_number()

        # Candidate criminals: active accounts deep enough in negative rep to be
        # wanted. The per-row re-read re-confirms on the locked row.
        candidate_ids = (
            db.query(Player.id)
            .filter(
                Player.is_active.is_(True),
                Player.personal_reputation <= SYSTEM_BOUNTY_CRIMINAL_THRESHOLD,
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

                added = BountyService.accrue_system_bounty_pot(player, period)

                db.commit()  # accrual + anchor advance commit atomically
                if added > 0:
                    result["criminals"] += 1
                    result["credits"] += added
            except Exception:
                logger.exception(
                    "Bounty-accrual sweep: accrual failed for player %s",
                    player_id,
                )
                db.rollback()

        if result["criminals"]:
            logger.info(
                "Bounty-accrual sweep: canonical-day %d — grew %d criminal "
                "pot(s) by %d cr total",
                period, result["criminals"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Bounty-accrual sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_port_operating_costs_sync() -> Dict[str, int]:
    """Charge each player-owned port its accrued maintenance/upkeep and force-sell
    any port that has been insolvent for the canon threshold (WO-B3). This is the
    AUTONOMOUS half of the operating-cost engine — port_ownership_service.
    accrue_operating_costs is the LAZY, idempotent engine (charge math +
    insolvency 3-month auto-sell); before this sweep it ONLY fired via the manual
    POST /stations/{id}/accrue-costs endpoint, so an unvisited port never paid
    upkeep and an abandoned port never force-sold. This sweep is the scheduler
    shell that DRIVES that existing engine under the standard discipline. It does
    NOT reimplement any cost math, the maintenance rate, or the insolvency
    threshold — it calls accrue_operating_costs per port and lets it manage its
    own charge, anchor advance, insolvency tally, and inline auto_sell_insolvent.

    DISCIPLINE — mirrors _run_bounty_accrual_sweep_sync / _run_idle_income_sweep_
    sync EXACTLY (this runs on the LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead of
        double-charging (the lock auto-releases on the first commit), then commit
        immediately to CLAIM the sweep without pinning the lock across the
        iteration — MANDATORY: if this commit is omitted, the first per-station
        commit below would release the advisory lock mid-iteration and let a
        second instance enter concurrently (double-accrual);
      * a candidate-id query (no batch lock), then a per-station with_for_update
        re-read so a concurrent manual /accrue-costs call or a transfer can't race
        the charge. NOTE: accrue_operating_costs calls _lock_station internally
        (.with_for_update()) — that IS the row lock; we do NOT re-lock here, and
        we acquire NO other row lock before calling it, preserving the service's
        station-then-player-ascending lock order (auto_sell_insolvent locks a
        Player row for the reputation hit);
      * per-station commit and per-station try/except — one bad port cannot abort
        the batch or roll back already-charged ports.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — the charge is a DEBIT and the
    insolvency path force-SELLS): the durable per-station anchor is the wall-clock
    ISO timestamp under Station.ownership['costs_accrued_at'], advanced in the
    SAME per-station transaction as the debit by accrue_operating_costs (whole
    elapsed days only; sub-day remainder stays pending). A re-run, duplicate wake,
    or restart within the same canonical day re-reads the anchor, computes
    elapsed_days <= 0, and returns status 'current' — NEVER a double-charge and
    NEVER a double-increment of the insolvency clock (so NEVER a double
    force-sell). Additive JSONB only; NO migration, NO new column.

    CANDIDATE GATE: owned, player-ownable, not-destroyed stations. owner_id IS NOT
    NULL is the real ownership gate (accrue_operating_costs no-ops on unowned
    stations anyway); is_player_ownable excludes structural non-ownable stations;
    is_destroyed excludes ruined ones. Listability flags (spacedock/tradedock/…)
    gate SALE, not cost accrual — an owned port pays upkeep regardless.

    Returns {"ports": n_charged, "insolvent": n_force_sold}; ``ports`` counts
    stations that took a NONZERO charge (a station already current for the period
    is not counted). All zero on the lock-held / nothing-due no-op paths."""
    from src.core.database import SessionLocal
    from src.models.station import Station
    from src.services import port_ownership_service

    result = {"ports": 0, "insolvent": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the bounty / idle-income / stipend sweeps): claim the
        # sweep, then iterate. MANDATORY — see the docstring.
        db.commit()

        # Candidate ports: owned + player-ownable + not destroyed. IDs only (no
        # batch lock); the per-station re-read takes the row lock.
        candidate_ids = (
            db.query(Station.id)
            .filter(
                Station.owner_id.isnot(None),
                Station.is_player_ownable.is_(True),
                Station.is_destroyed.is_(False),
            )
            .all()
        )

        for (station_id,) in candidate_ids:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None or station.owner_id is None:
                    db.rollback()  # release row lock; nothing to do
                    continue

                # accrue_operating_costs re-locks the station internally
                # (_lock_station), charges whole elapsed canonical days, advances
                # the durable anchor, tallies insolvency months, and inline-
                # force-sells at the canon threshold — all in THIS transaction,
                # no commit of its own.
                outcome = port_ownership_service.accrue_operating_costs(db, station)

                db.commit()  # charge + anchor advance (+ any force-sell) atomic

                if (outcome.get("charged") or 0) > 0:
                    result["ports"] += 1
                if outcome.get("insolvency"):
                    result["insolvent"] += 1
            except Exception:
                logger.exception(
                    "Port operating-cost sweep: accrual failed for station %s",
                    station_id,
                )
                db.rollback()

        if result["ports"] or result["insolvent"]:
            logger.info(
                "Port operating-cost sweep: charged %d port(s); %d force-sold "
                "(insolvent)",
                result["ports"], result["insolvent"],
            )
        return result
    except Exception:
        logger.exception("Port operating-cost sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_station_recovery_sync() -> Dict[str, int]:
    """Auto-rebuild every destroyed station whose 24-CANONICAL-hour recovery
    window has elapsed (WO-DBB-EC6; FEATURES/economy/trading.md § Destruction
    & recovery). This is the AUTONOMOUS half of the station destruction/recovery
    engine — station_service holds the lazy, idempotent rebuild logic
    (recover_station: rebuild commodities to 50% of the destruction-time
    snapshot, clear the destroyed flag/timer, zero re-purchasable defenses).
    This sweep is the scheduler shell that DRIVES that engine. It does NOT
    reimplement the 24h window or the 50% rebuild fraction (both CANON, owned by
    station_service) — it queries due stations and calls recover_station per row.

    DISCIPLINE — mirrors _run_port_operating_costs_sync EXACTLY (runs on the
    LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-rebuilding, then commit IMMEDIATELY to claim the sweep without
        pinning the lock across the iteration — MANDATORY: omitting this commit
        lets the first per-station commit below release the lock mid-iteration
        and a second instance enter concurrently;
      * a candidate-id query (no batch lock) pre-filtered to destroyed stations
        whose absolute deadline (recovery_time) has passed, then a per-station
        with_for_update re-read so a concurrent path can't race the rebuild;
      * per-station commit and per-station try/except — one bad station cannot
        abort the batch or roll back already-rebuilt stations.

    IDEMPOTENCY ACROSS RESTARTS: the durable per-station state is the existing
    Station.is_destroyed flag + Station.recovery_time deadline + the
    ownership['destroyed_at'] canonical-hours anchor. is_recovery_due re-checks
    the canonical-hours window inside the row lock; recover_station re-checks the
    destroyed flag. A re-run, duplicate wake, or restart finds is_destroyed False
    (already rebuilt) or the window not yet elapsed and no-ops — NEVER a
    double-rebuild. Additive JSONB only; NO migration, NO new column.

    CANDIDATE GATE: is_destroyed = True AND recovery_time <= now (the absolute
    wall-clock deadline encodes the scaled 24-canonical-hour window). The
    per-station is_recovery_due() then confirms via the canonical-hours anchor
    before recover_station runs (defensive against a stale deadline column).

    Returns {"recovered": n_rebuilt}; counts stations actually rebuilt this
    sweep. Zero on the lock-held / nothing-due no-op paths."""
    from src.core.database import SessionLocal
    from src.models.station import Station
    from src.services import station_service

    result = {"recovered": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the port-cost / bounty / idle-income sweeps): claim the
        # sweep, then iterate. MANDATORY — see the docstring.
        db.commit()

        now = datetime.now(UTC)

        # Candidate stations: destroyed + deadline elapsed. IDs only (no batch
        # lock); the per-station re-read takes the row lock. is_recovery_due
        # re-confirms via the canonical-hours anchor inside the lock.
        candidate_ids = (
            db.query(Station.id)
            .filter(
                Station.is_destroyed.is_(True),
                Station.recovery_time.isnot(None),
                Station.recovery_time <= now,
            )
            .all()
        )

        for (station_id,) in candidate_ids:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None or not station.is_destroyed:
                    db.rollback()  # release row lock; nothing to do
                    continue

                # Re-confirm the canonical-hours window inside the row lock
                # (restart-proof anchor); skip if not actually due yet.
                if not station_service.is_recovery_due(station, now):
                    db.rollback()
                    continue

                outcome = station_service.recover_station(db, station, now)

                db.commit()  # rebuild + flag clear atomic

                if outcome.get("status") == "recovered":
                    result["recovered"] += 1
            except Exception:
                logger.exception(
                    "Station recovery sweep: rebuild failed for station %s",
                    station_id,
                )
                db.rollback()

        if result["recovered"]:
            logger.info(
                "Station recovery sweep: rebuilt %d destroyed station(s) at "
                "50%% inventory",
                result["recovered"],
            )
        return result
    except Exception:
        logger.exception("Station recovery sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_reclaim_flag_sweep_sync() -> Dict[str, int]:
    """Stamp the inactivity-reclamation flag on planets whose owner has gone
    inactive, and clear it on any planet whose owner is no longer stale (PL4b —
    planet abandonment/reclaim, master §2.1). This is the AUTONOMOUS half of the
    abandonment lifecycle — abandonment_service.flag_inactive_planets is the
    ADVISORY, idempotent, REVERSIBLE flagger (it NEVER deletes a row, NEVER
    reassigns ownership; it only sets/clears planets.reclaimable_at). This sweep
    is the scheduler shell that DRIVES that engine under the standard discipline.
    It does NOT reimplement the 90-day inactivity rule, the grace window, or any
    compensation math — it calls flag_inactive_planets once per cadence.

    DISCIPLINE — mirrors _run_station_recovery_sync / _run_port_operating_costs_
    sync / _run_bounty_accrual_sweep_sync (this runs on the LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead of
        double-flagging (the lock auto-releases on the first commit), then commit
        immediately to CLAIM the sweep before the flag work, so the flag commit
        does not release the advisory lock mid-iteration;
      * a single set/clear pass (flag_inactive_planets) committed once — the pass
        touches only the marker column, so a batch commit is safe (no per-row
        money movement happens here; the credit movement is the PLAYER-driven
        reclaim route, gated behind grace + tenure).

    IDEMPOTENCY ACROSS RESTARTS: the durable per-planet anchor is the marker
    itself (planets.reclaimable_at — NULL = not flagged). A re-run, duplicate
    wake, or restart re-evaluates the SAME condition (owner stale? already
    flagged?) and is a no-op for steady-state rows — NEVER a double-flag and (the
    important safety property) NEVER an auto-reclaim: this sweep ONLY moves the
    advisory marker; ownership only changes when a PLAYER fires the reclaim route
    AFTER the 90d + 7d-grace gates AND the displaced owner's 7d tenure floor.
    Additive marker write only; NO new table, NO money faucet.

    Returns {"flagged": n_newly_flagged, "cleared": n_cleared}; both zero on the
    lock-held no-op path."""
    from src.core.database import SessionLocal
    from src.services import abandonment_service

    result = {"flagged": 0, "cleared": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before the flag work (same
        # rationale as the recovery / port-cost / bounty sweeps): claim the
        # sweep, then run the pass.
        db.commit()

        outcome = abandonment_service.flag_inactive_planets(db)
        db.commit()  # the set/clear pass commits as one (marker-only writes)
        result["flagged"] = int(outcome.get("flagged", 0))
        result["cleared"] = int(outcome.get("cleared", 0))

        if result["flagged"] or result["cleared"]:
            logger.info(
                "Reclaim-flag sweep: flagged %d inactive-owner planet(s), "
                "cleared %d returned-owner flag(s)",
                result["flagged"], result["cleared"],
            )
        return result
    except Exception:
        logger.exception("Reclaim-flag sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_price_recompute_flush_sync() -> int:
    """Settle every station flagged pending_price_recomputation (WO-DBB-EC4,
    ADR-0051 SK30) — the deferred half of the per-station 1s price-recompute
    rate limit. The hot market-read path debounces full recomputes to once per
    ~1 wall-clock second per station; a suppressed recompute sets
    Station.pending_price_recomputation. This sweep DRIVES
    TradingService.flush_pending_recomputes (which holds the per-station lock +
    flag re-check + per-station try/except) so a flagged station does not stay
    stale.

    DISCIPLINE — own SessionLocal (never the request session, never the async
    engine), commit after the flush, never reuse the request connection. The
    durable per-station state is the pending_price_recomputation flag (survives
    a restart; a re-run finds the flag cleared and no-ops). No advisory lock is
    taken here: flush_pending_recomputes locks each station row individually, so
    a second instance racing the sweep is serialized per-row by with_for_update
    and the post-lock flag re-check makes a duplicate reprice a no-op.

    Returns the count of stations recomputed (0 on the nothing-pending path)."""
    from src.core.database import SessionLocal
    from src.services.trading_service import TradingService

    db = SessionLocal()
    try:
        flushed = TradingService(db).flush_pending_recomputes()
        db.commit()
        return flushed
    except Exception:
        logger.exception("Price-recompute flush sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()


def _run_price_alert_sweep_sync() -> int:
    """Evaluate every active PriceAlert against current MarketPrice rows and
    emit price_alert_triggered WS frames to any owner whose threshold was
    crossed.

    Before this sweep existed, sweep_price_alerts (price_alert_service) was
    never called from anywhere — alerts only fired from the request path when a
    *player* traded the exact (station, commodity) pair, so NPC-driven price
    moves (Loop A trades via npc_trading_service, flush_pending_recomputes,
    production restocks) silently skipped threshold evaluation. This sweep
    closes that gap: every active alert is checked on the scheduler cadence so
    an NPC move past an alert threshold triggers delivery within one tick.

    PRICE REFERENCE — uses MarketPrice.sell_price (what the station charges
    players to buy the commodity) as the canonical price fed to
    evaluate_price_alerts.  This matches the dominant alert use-case ("alert me
    when commodity X costs more than Y at this station") and is consistent with
    the midpoint published by publish_trade_tick.  Once the PriceAlert model
    gains a price_kind column the injected price_lookup can be split per kind
    without touching this sweep.

    DISCIPLINE — own SessionLocal (never the request session, never the async
    engine), commit after the sweep so last_triggered_at stamps persist,
    close on exit.  No advisory lock needed: sweep_price_alerts does no
    row-locking (it only writes last_triggered_at on matched alerts) and a
    second instance racing the sweep would merely re-evaluate cooldowns, which
    are idempotent.

    Returns the count of alerts fired (0 when nothing crossed a threshold)."""
    from src.core.database import SessionLocal
    from src.models.market_transaction import MarketPrice
    from src.services.price_alert_service import sweep_price_alerts

    db = SessionLocal()
    try:
        def price_lookup(station_id, commodity):
            row = (
                db.query(MarketPrice)
                .filter(
                    MarketPrice.station_id == station_id,
                    MarketPrice.commodity == commodity,
                )
                .first()
            )
            if row is None:
                return None
            return float(row.sell_price)

        fired = sweep_price_alerts(db, price_lookup)
        db.commit()
        return len(fired)
    except Exception:
        logger.exception("Price-alert sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Price-history time-series sweep (WO-ECON-MKT-TIMESERIES) — hourly
# PriceHistory snapshots + daily/weekly rollups + retention pruning
# ---------------------------------------------------------------------------

def _price_history_rollup(
    db: Session, snapshot_type: str, source_type: str,
    window_start: datetime, window_end: datetime, target_date: datetime,
) -> int:
    """Aggregate every ``source_type`` PriceHistory row in
    [window_start, window_end) into one ``snapshot_type`` row per (station,
    commodity), dated ``target_date``. Shared by the daily (hourly->daily) and
    weekly (daily->weekly) rollups — same shape, different source/target
    granularity. Idempotent: skips any (station, commodity) that already has
    a ``snapshot_type`` row dated ``target_date`` (restart-safe — a missed or
    duplicate wake is a clean no-op, mirroring the daily EconomicMetrics
    anchor)."""
    from src.models.market_transaction import PriceHistory

    already = set(
        db.query(PriceHistory.station_id, PriceHistory.commodity)
        .filter(
            PriceHistory.snapshot_type == snapshot_type,
            PriceHistory.snapshot_date == target_date,
        )
        .all()
    )

    source_rows = (
        db.query(PriceHistory)
        .filter(
            PriceHistory.snapshot_type == source_type,
            PriceHistory.snapshot_date >= window_start,
            PriceHistory.snapshot_date < window_end,
        )
        .all()
    )
    by_key: Dict[Tuple[Any, str], List[Any]] = {}
    for row in source_rows:
        by_key.setdefault((row.station_id, row.commodity), []).append(row)

    written = 0
    for (station_id, commodity), rows in by_key.items():
        if (station_id, commodity) in already:
            continue
        n = len(rows)
        total_volume = sum(r.daily_volume for r in rows)
        total_txns = sum(r.transactions_count for r in rows)
        db.add(PriceHistory(
            station_id=station_id,
            commodity=commodity,
            buy_price=round(sum(r.buy_price for r in rows) / n),
            sell_price=round(sum(r.sell_price for r in rows) / n),
            quantity=round(sum(r.quantity for r in rows) / n),
            daily_volume=total_volume,
            transactions_count=total_txns,
            average_transaction_size=(
                float(total_volume) / total_txns if total_txns else 0.0
            ),
            demand_level=sum(r.demand_level for r in rows) / n,
            supply_level=sum(r.supply_level for r in rows) / n,
            snapshot_date=target_date,
            snapshot_type=snapshot_type,
        ))
        written += 1
    db.flush()
    return written


def sweep_price_history(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Core PriceHistory sweep logic — hourly snapshot of every current
    MarketPrice row, plus daily/weekly rollups on their calendar boundaries.

    Deliberately takes an injected ``db`` and does no session lifecycle,
    advisory-lock, or commit/rollback of its own (mirrors
    TradingService.flush_pending_recomputes / price_alert_service.
    sweep_price_alerts) — that discipline lives in the
    ``_run_price_history_sweep_sync`` wrapper below. This split is what makes
    the sweep unit-testable directly against the ``db`` fixture rather than
    only provable live.

    HOURLY — one row per (station, commodity) with a MarketPrice row,
    snapshotting price/quantity/demand/supply as of ``now`` (hour-truncated)
    plus interval volume (sum of MarketTransaction.quantity / count over the
    trailing hour, one aggregate GROUP BY query — no per-station loop).
    Idempotent within the hour: a (station, commodity) that already has an
    hourly row dated this hour is skipped, so a second tick inside the same
    hour never duplicates.

    DAILY / WEEKLY — rolled up via the shared ``_price_history_rollup``
    helper only on the relevant calendar boundary (daily: the first tick of
    a new UTC day, rolling up yesterday's hourly rows; weekly: the first
    tick of a new ISO week — Monday — rolling up the last 7 days' daily
    rows), so the aggregation query only ever runs once per period.

    PRUNING — deletes hourly rows older than PRICE_HISTORY_HOURLY_RETENTION_
    DAYS and daily rows older than PRICE_HISTORY_DAILY_RETENTION_DAYS every
    tick (cheap — indexed on snapshot_date); weekly rows are never pruned
    (NO-CANON retention policy, see the constants above).

    Returns {"hourly": int, "daily": int, "weekly": int, "pruned": int} —
    counts of rows written/deleted this call.
    """
    from src.models.market_transaction import MarketPrice, MarketTransaction, PriceHistory
    from sqlalchemy import func as sa_func

    now = now or datetime.utcnow()
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    day_start = hour_start.replace(hour=0)

    # --- Hourly snapshot -----------------------------------------------
    already_hourly = set(
        db.query(PriceHistory.station_id, PriceHistory.commodity)
        .filter(
            PriceHistory.snapshot_type == "hourly",
            PriceHistory.snapshot_date == hour_start,
        )
        .all()
    )

    vol_rows = (
        db.query(
            MarketTransaction.station_id,
            MarketTransaction.commodity,
            sa_func.coalesce(sa_func.sum(MarketTransaction.quantity), 0),
            sa_func.count(MarketTransaction.id),
        )
        .filter(MarketTransaction.timestamp >= hour_start)
        .group_by(MarketTransaction.station_id, MarketTransaction.commodity)
        .all()
    )
    volume_by_key = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in vol_rows}

    hourly_written = 0
    for mp in db.query(MarketPrice).all():
        key = (mp.station_id, mp.commodity)
        if key in already_hourly:
            continue
        volume, txn_count = volume_by_key.get(key, (0, 0))
        db.add(PriceHistory(
            station_id=mp.station_id,
            commodity=mp.commodity,
            buy_price=mp.buy_price,
            sell_price=mp.sell_price,
            quantity=mp.quantity,
            daily_volume=volume,
            transactions_count=txn_count,
            average_transaction_size=(float(volume) / txn_count if txn_count else 0.0),
            demand_level=mp.demand_level,
            supply_level=mp.supply_level,
            snapshot_date=hour_start,
            snapshot_type="hourly",
        ))
        hourly_written += 1
    db.flush()

    # --- Daily / weekly rollups, only on their calendar boundary --------
    daily_written = 0
    if hour_start == day_start:  # first tick of a new UTC day
        daily_written = _price_history_rollup(
            db, "daily", "hourly",
            window_start=day_start - timedelta(days=1), window_end=day_start,
            target_date=day_start - timedelta(days=1),
        )

    weekly_written = 0
    if hour_start == day_start and day_start.weekday() == 0:  # Monday
        weekly_written = _price_history_rollup(
            db, "weekly", "daily",
            window_start=day_start - timedelta(days=7), window_end=day_start,
            target_date=day_start - timedelta(days=7),
        )

    # --- Retention pruning -----------------------------------------------
    hourly_cutoff = now - timedelta(days=PRICE_HISTORY_HOURLY_RETENTION_DAYS)
    daily_cutoff = now - timedelta(days=PRICE_HISTORY_DAILY_RETENTION_DAYS)
    pruned = (
        db.query(PriceHistory)
        .filter(PriceHistory.snapshot_type == "hourly", PriceHistory.snapshot_date < hourly_cutoff)
        .delete(synchronize_session=False)
    )
    pruned += (
        db.query(PriceHistory)
        .filter(PriceHistory.snapshot_type == "daily", PriceHistory.snapshot_date < daily_cutoff)
        .delete(synchronize_session=False)
    )

    return {
        "hourly": hourly_written, "daily": daily_written,
        "weekly": weekly_written, "pruned": pruned,
    }


def _run_price_history_sweep_sync() -> Dict[str, int]:
    """Own-session wrapper around ``sweep_price_history`` — SessionLocal +
    advisory lock + commit/rollback, same discipline as every other sweep in
    this module. A second gameserver instance racing the same tick skips
    (pg_try_advisory_xact_lock) rather than double-writing; a mid-sweep
    failure rolls back the whole tick (nothing partially written — the next
    hourly wake retries cleanly, same as the EconomicMetrics snapshot)."""
    from src.core.database import SessionLocal

    not_written = {"hourly": 0, "daily": 0, "weekly": 0, "pruned": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_written

        result = sweep_price_history(db)
        db.commit()  # releases the xact lock
        return result
    except Exception:
        logger.exception("Price-history sweep failed")
        db.rollback()
        return not_written
    finally:
        db.close()


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
            {"key": _ADVISORY_LOCK_KEY},
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
        # N-V4: region-scoped ops alert. Fans out to the affected region room
        # AND the admin/ops fan-out (the standing admin alert channel). Routes
        # off the event type (not sector_id) since detection is region-level.
        # Best-effort, same as every broadcast here — a WS failure is logged,
        # never raised, so the underlying flood op is unaffected.
        if event.get("type") == "npc.coordinated_genocide_detected":
            region_id = event.get("region_id")
            if region_id is not None:
                try:
                    await connection_manager.broadcast_to_region(
                        str(region_id), dict(event)
                    )
                except Exception:
                    logger.exception(
                        "NPC scheduler: region broadcast failed for %s",
                        event.get("type"),
                    )
            try:
                await connection_manager.broadcast_to_admins(dict(event))
            except Exception:
                logger.exception(
                    "NPC scheduler: admin broadcast failed for %s",
                    event.get("type"),
                )
            continue

        # WO-G4: genesis_progress is a PERSONAL frame to the planet owner — not a
        # sector room broadcast. Route it via the per-user primitive
        # (connection_manager.send_personal_message(user_id: str, message)).
        # Best-effort like every broadcast here: a WS hiccup is logged, never
        # raised, so the underlying genesis completion is unaffected. Falls
        # through to nothing else (continue) — owner_id absent => no recipient.
        if event.get("type") == "genesis_progress":
            owner_id = event.get("owner_id")
            if owner_id is not None:
                try:
                    await connection_manager.send_personal_message(
                        str(owner_id), dict(event)
                    )
                except Exception:
                    logger.exception(
                        "NPC scheduler: genesis_progress send failed for owner %s",
                        owner_id,
                    )
            continue

        sector_id = event.get("sector_id")
        if sector_id is None:
            continue
        try:
            await connection_manager.broadcast_to_sector(int(sector_id), dict(event))
        except Exception:
            logger.exception("NPC scheduler: broadcast failed for %s", event.get("type"))


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
            {"key": _ADVISORY_LOCK_KEY},
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
    candidate query (only non-empty presence lists); per-sector commit + isolated
    try/except. IDEMPOTENT — re-removing an already-absent player is a no-op, so
    the lock-releases-on-first-commit property is harmless here. Reads a wall-clock
    last-seen, mutates only the JSONB list. No migration, no new row.
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
        sectors = (
            db.query(Sector)
            .filter(text("jsonb_array_length(players_present) > 0"))
            .all()
        )
        for sec in sectors:
            try:
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
                    getattr(sec, "id", "?"),
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
