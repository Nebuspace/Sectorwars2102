"""Shared state for the scheduler package (WO-QUALITY-techdebt-scheduler-split).

Constants, per-sweep advisory-lock keys, the canonical schedule clock,
``resolve_schedule_block``, the durable sweep-anchor helpers, and
``_broadcast_events`` — everything every other scheduler submodule imports
from. Deliberately has ZERO imports from sibling scheduler submodules (only
from top-level src.* modules) to keep the package import graph acyclic.

Moved verbatim from the old ``npc_scheduler_service.py`` — see that file's
original module docstring (now on ``src/services/scheduler/core_loop.py``,
home of the host loop) for the scheduler's overall design.
"""

import hashlib
import logging
import os
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.npc_character import NPCStatus

logger = logging.getLogger(__name__)


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
# WO-SCHED-CADENCE-DRIFT: durable, wall-clock, restart-safe last-run anchors
# for the 5 sub-daily loop-table sweeps (WO-ECON-CONTRACT-1-KERNEL / WO-CMB-
# SUSPECT-LIFE-1 / WO-RT-TEAM-REP / WO-PIRATE-ECO-2 held wiring). These used
# to fire on `elapsed % INTERVAL_SECONDS == 0`, where `elapsed` is a pure
# ITERATION counter (+= TICK_SECONDS every loop pass) — NOT wall-clock: each
# iteration's real wall-clock duration is TICK_SECONDS plus every due sweep's
# awaited work THAT iteration (the to_thread calls below are sequential, not
# concurrent), so under real load `elapsed % 300 == 0` fires every 5
# ITERATIONS, not every 300 wall-clock seconds — the more work a tick does,
# the slower every gated sweep's TRUE cadence gets, unboundedly. `elapsed`
# also resets to 0 on every process restart, so a longer-interval sweep can
# be starved indefinitely on a frequently-restarted host. Fixed the same way
# the weekly job already was (see _WEEKLY_DECAY_STATE_KEY above): a Galaxy.
# state anchor holding a wall-clock ISO timestamp, checked via
# `now() - last_run_at >= interval` — see _sweep_due_and_advance.
_SUSPECT_CLEAR_STATE_KEY = "suspect_clear_last_run_at"
_CONTRACT_GENERATION_STATE_KEY = "contract_generation_last_run_at"
_CONTRACT_EXPIRE_STATE_KEY = "contract_expire_last_run_at"
_TEAM_REPUTATION_SWEEP_STATE_KEY = "team_reputation_sweep_last_run_at"
_PIRATE_ECOSYSTEM_TICK_STATE_KEY = "pirate_ecosystem_tick_last_run_at"
# Deliberately NO coarse elapsed pre-filter for these 4 (unlike the
# day-scale anchors above) — their intervals (300-1800s) are close enough to
# TICK_SECONDS that a coarse pre-filter would reintroduce the very drift
# being fixed here; _sweep_due_and_advance is called every iteration and a
# cheap Galaxy-row read + advisory-lock attempt every 60s is negligible
# load. PECO's interval is daily (86400s default) — large enough that,
# matching the citizen-rebake/ARIA-prune/retention convention above, a
# coarse pre-filter IS safe (drift on a 55-minute pre-filter is negligible
# against a day-scale target) and avoids a wasted per-60s check ~23 of 24
# hours a day. Offset from the other day-scale pre-filters (45m/50m) to
# avoid stacking on the same wake.
PIRATE_ECOSYSTEM_TICK_CHECK_SECONDS = int(
    os.environ.get("PIRATE_ECOSYSTEM_TICK_CHECK_SECONDS", str(55 * 60))
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

# Sustained-reputation-drip sweep cadence (factions-and-teams.md:229-230,
# WO-PROG-SUSTAINED-DRIPS). Like the port-cost / station-recovery / reclaim-
# flag sweeps, the cadence is a COARSE elapsed pre-filter (so we don't take
# the advisory lock + scan players every 60s); the actual once-per-canonical-
# day-per-player drip guarantee comes from the DURABLE per-player anchor —
# Player.settings["sustained_tier"]["last_drip_day"] — which survives a
# restart (the process-relative elapsed counter resets; the stored anchor
# does not). Offset to 60m so it does not share a wake with the other coarse
# probes (decay 15m / faucet 20m / snapshot 25m / idle 30m / stipend 35m /
# bounty 40m / port-costs 45m / station-recovery 50m / reclaim-flag 55m).
# CADENCE IS NO-CANON: canon gives the 7-day sustained threshold and the
# per-day drip figures; the background SWEEP cadence is an implementation
# choice — flagged for a DECISIONS.md ruling.
SUSTAINED_REP_DRIP_CHECK_SECONDS = int(
    os.environ.get("SUSTAINED_REP_DRIP_CHECK_SECONDS", str(60 * 60))
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

# NPC contract generation + expiry sweep (WO-ECON-CONTRACT-1-KERNEL). The
# generator posts new cargo_delivery contracts on a station-scan cadence
# (fast enough for a fresh board without hammering Postgres on every 60s
# wake); the expiry sweep prunes posted-but-never-accepted contracts past
# their deadline on a tighter cadence since a stale board is more visible
# to players than a slow generation refresh. Both NO-CANON — contracts.md
# is silent on either cadence; proposed to DECISIONS alongside the
# generator's own pool/quantity/deadline sizing constants.
CONTRACT_GENERATION_SWEEP_SECONDS = int(
    os.environ.get("CONTRACT_GENERATION_SWEEP_SECONDS", str(15 * 60))
)
CONTRACT_EXPIRE_SWEEP_SECONDS = int(
    os.environ.get("CONTRACT_EXPIRE_SWEEP_SECONDS", str(5 * 60))
)

# Suspect auto-clear sweep (WO-CMB-SUSPECT-LIFE-1 held wiring) —
# suspect_service.clear_expired_suspects. NO-CANON: ships.md:293 names the
# auto-clear BEHAVIOR ("auto-clears at suspect_until") but not a sweep
# interval; proposed to DECISIONS — 5 minutes keeps the cleared flag
# reasonably prompt without hammering the player table on every 60s wake.
SUSPECT_CLEAR_SWEEP_SECONDS = int(
    os.environ.get("SUSPECT_CLEAR_SWEEP_SECONDS", str(5 * 60))
)

# Team-reputation recalculation sweep (WO-RT-TEAM-REP held wiring) —
# team_reputation_service.sweep_due_team_reputations.
# RECALCULATION_INTERVAL (team_reputation_service.py) is 1 day; checking
# well inside that window keeps a due team's recalculation from lagging
# visibly behind its next_recalculation stamp without adding real load
# (the query is a single indexed next_recalculation <= now filter).
# NO-CANON: canon is silent on sweep cadence, only the daily recalculation
# interval itself.
TEAM_REPUTATION_SWEEP_SECONDS = int(
    os.environ.get("TEAM_REPUTATION_SWEEP_SECONDS", str(30 * 60))
)

# Pirate-ecosystem weekly growth + evolution tick sweep (WO-PIRATE-ECO-2
# held wiring) — pirate_ecosystem_service.run_weekly_tick / evolution_tick.
# Both engines are idempotent per their own window (growth: once per UTC
# week via last_growth_tick_at; evolution: a day-granularity threshold
# re-evaluated fresh every call), so sweeping more often than weekly is
# safe — a daily cadence keeps a freshly-due evolution roll from waiting up
# to a week to be noticed, without scanning every active region's holdings
# on every 60s tick. NO-CANON: pirate-ecosystem.md names the engines' own
# windows, not an outer sweep interval; proposed to DECISIONS.
PIRATE_ECOSYSTEM_TICK_SWEEP_SECONDS = int(
    os.environ.get("PIRATE_ECOSYSTEM_TICK_SWEEP_SECONDS", str(24 * 60 * 60))
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


def _mnemonic_lock_key(code: str) -> int:
    """Pack a 4-character ASCII mnemonic into a lock-key int, exactly the
    scheme already hand-applied for ``_CITIZEN_REBAKE_LOCK_KEY`` ('GCRB') and
    ``_PRESENCE_SWEEP_LOCK_KEY`` ('PRSW') — four ASCII bytes packed
    big-endian give a value in ``[0, 2**32)``, always non-negative and well
    inside the signed-63-bit range ``pg_try_advisory_xact_lock``'s bigint
    argument requires. Distinct 4-character codes are byte-for-byte
    distinct, so no two mnemonic-derived keys can ever collide as long as
    their codes differ (a guarantee, not a probabilistic hash property) —
    this is what makes the per-sweep-type keys below safe to hand-assign
    without a collision check."""
    code_bytes = code.encode("ascii")
    if len(code_bytes) != 4:
        raise ValueError(f"lock-key mnemonic must be exactly 4 ASCII chars: {code!r}")
    return int.from_bytes(code_bytes, "big")


# ---------------------------------------------------------------------------
# Per-sweep-type advisory-lock keys (WO-RT-LOCK-ACTIVATE)
#
# Before this, every sweep below shared the single global _ADVISORY_LOCK_KEY
# with the main NPC tick (_run_due_ticks_sync) — a tick that runs Loop A/B/C
# across the whole galaxy could hold that key for minutes, and every other
# sweep sharing the key would skip (pg_try_advisory_xact_lock) or, for the
# one blocking acquirer, stall behind it. Each sweep below now gets its OWN
# derived key so it only ever serializes against another instance of ITSELF
# (the actual collision this locking exists to prevent — two gameserver
# instances double-running the SAME sweep), never against the main tick or
# any unrelated sweep. The main tick keeps _ADVISORY_LOCK_KEY; no sweep here
# uses it anymore.
# ---------------------------------------------------------------------------
_WEEKLY_DECAY_LOCK_KEY = _mnemonic_lock_key("WKDY")
_GENESIS_COMPLETION_LOCK_KEY = _mnemonic_lock_key("GNCP")
_PLANETARY_ADVANCE_LOCK_KEY = _mnemonic_lock_key("PLAD")
# Covers the whole 7-phase governance sweep, INCLUDING the nested Phase-6
# treasury reconciliation call (_run_treasury_reconciliation_gated) — that
# helper takes an already-open, already-locked session and acquires no lock
# of its own, so it rides this same key rather than double-keying.
_GOVERNANCE_SWEEP_LOCK_KEY = _mnemonic_lock_key("GOVN")
_CONSTRUCTION_ADVANCE_LOCK_KEY = _mnemonic_lock_key("CNAD")
# Covers the daily economic-metrics snapshot, INCLUDING the nested
# inflation/health/volatility/leaders enrichment call
# (_compute_daily_economic_enrichment) for the same no-lock-of-its-own
# reason as the governance sweep above.
_ECONOMIC_METRICS_LOCK_KEY = _mnemonic_lock_key("ECMT")
_IDLE_INCOME_LOCK_KEY = _mnemonic_lock_key("IDLI")
_DAILY_STIPEND_LOCK_KEY = _mnemonic_lock_key("STIP")
_BOUNTY_ACCRUAL_LOCK_KEY = _mnemonic_lock_key("BNTY")
_SUSTAINED_DRIP_LOCK_KEY = _mnemonic_lock_key("SDRP")
_PORT_OPERATING_COSTS_LOCK_KEY = _mnemonic_lock_key("PORT")
_STATION_RECOVERY_LOCK_KEY = _mnemonic_lock_key("STRC")
_RECLAIM_FLAG_LOCK_KEY = _mnemonic_lock_key("RCLM")
_PRICE_HISTORY_LOCK_KEY = _mnemonic_lock_key("PXHS")
_ROUTE_RUNS_RETENTION_LOCK_KEY = _mnemonic_lock_key("RTRT")
_ORPHAN_SCHEDULE_REPAIR_LOCK_KEY = _mnemonic_lock_key("ORPH")
_SEED_TRADER_ROSTERS_LOCK_KEY = _mnemonic_lock_key("SEED")
_LAW_PATROL_DISPERSAL_LOCK_KEY = _mnemonic_lock_key("LAWP")
_STRANDED_RELOCATE_LOCK_KEY = _mnemonic_lock_key("STRN")
_TRADER_NOTORIETY_LOCK_KEY = _mnemonic_lock_key("NTRY")
_TRADER_MISSION_LOCK_KEY = _mnemonic_lock_key("TMSN")
_BULK_FILL_TRADERS_LOCK_KEY = _mnemonic_lock_key("BFIL")
# Player retention-SIGNAL sweep (WO-RE2) — distinct from the unrelated
# RouteOptimizationRun retention job just above (_ROUTE_RUNS_RETENTION_LOCK_KEY).
_RETENTION_SWEEP_LOCK_KEY = _mnemonic_lock_key("RETN")
# Suspect auto-clear sweep (WO-CMB-SUSPECT-LIFE-1 held wiring) — own key,
# not the global one. suspect_service.clear_expired_suspects only writes
# already-expired rows (a second instance racing the sweep finds zero
# matching rows on its own pass), so this lock exists purely to stop two
# instances double-flushing the same rows in the same instant, not for
# correctness.
_SUSPECT_CLEAR_LOCK_KEY = _mnemonic_lock_key("SUSP")
# Pirate-ecosystem weekly growth + evolution tick sweep (WO-PIRATE-ECO-2
# held wiring) — own key; covers the whole per-region growth + per-holding
# evolution pass in one lock (mirrors the citizen-rebake sweep's own
# whole-pass-one-lock shape rather than a per-region key — growth's own
# per-window idempotence is already the finer-grained safety net).
_PIRATE_ECOSYSTEM_TICK_LOCK_KEY = _mnemonic_lock_key("PECO")
# NPC contract generation (WO-SCHED-LOOP-WEDGE refinement) — own key. The
# wrapper previously took NO lock at all (fine on a single instance); the
# decoupled generation task now gates its WRITE phase on this so two
# gameserver instances can't double-generate against the same galaxy in a
# multi-instance deployment. 'CGEN' = Contract GENeration.
_CONTRACT_GENERATION_LOCK_KEY = _mnemonic_lock_key("CGEN")
# NPC contract EXPIRE sweep (WO-DRIFT-econ-contract-sweep-advisory-lock,
# expire half — the generation half above shipped its CGEN lock in a921392).
# The wrapper previously took no lock at all; two gameserver instances could
# double-expire (and double-refund) the same contracts. 'CEXP' = Contract
# EXPire.
_CONTRACT_EXPIRE_LOCK_KEY = _mnemonic_lock_key("CEXP")

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
# Durable sweep-anchor helpers (WO-SCHED-CADENCE-DRIFT / WO-SCHED-LOOP-WEDGE)
# ---------------------------------------------------------------------------

def _read_sweep_anchor(db: Session, state_key: str):
    """Shared read half of the durable-anchor contract (WO-SCHED-CADENCE-
    DRIFT / WO-SCHED-LOOP-WEDGE): the stable-anchor-row lookup (oldest
    Galaxy by created_at — a dev re-bootstrap creates a NEWER galaxy;
    keying off the newest would reset every anchor and double-fire every
    sweep at once) and last-run parse, factored out so _sweep_is_due
    (read-only peek) and _sweep_due_and_advance (stamping check) can't
    drift apart on what "due" means. Returns (galaxy_row_or_None,
    last_run_datetime_or_None)."""
    from src.models.galaxy import Galaxy

    galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
    if galaxy is None:
        return None, None
    state = dict(galaxy.state or {})
    last_run_raw = state.get(state_key)
    if last_run_raw is None:
        return galaxy, None
    try:
        return galaxy, datetime.fromisoformat(last_run_raw)
    except (TypeError, ValueError):
        return galaxy, None


def _sweep_is_due(db: Session, state_key: str, interval_seconds: int, now: datetime) -> bool:
    """Read-only peek — WO-SCHED-LOOP-WEDGE. Same due/not-due answer as
    _sweep_due_and_advance, but never mutates or stamps anything. For
    gating whether to even START a (now cheap, but non-zero) gather+
    compute pass before its authoritative, stamping due-check happens at
    write time — see _run_contract_generation_sync. Not itself a
    correctness guarantee against concurrent instances (nothing here
    locks); the stamping call at write time, lock-gated, is the real one."""
    galaxy, last_run = _read_sweep_anchor(db, state_key)
    if galaxy is None:
        return False
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval_seconds


def _sweep_due_and_advance(
    db: Session, state_key: str, interval_seconds: int, now: datetime,
) -> bool:
    """WO-SCHED-CADENCE-DRIFT: the ONLY correctness guarantee for the
    sub-daily loop-table sweeps below — see the _SUSPECT_CLEAR_STATE_KEY
    block's comment for the full mechanism this replaces (iteration-counted
    `elapsed`, which drifts under load and resets on restart).

    Durable, wall-clock, restart-safe due-check-and-stamp. Mirrors
    _run_weekly_decay_sync's own Galaxy.state anchor discipline exactly,
    adapted from a canonical-week index to a wall-clock ISO timestamp; same
    "advance in the caller's own transaction, not here" discipline.

    Returns False (not due) without mutating anything if the interval
    hasn't elapsed, or if no Galaxy row exists yet. Returns True AND stamps
    the anchor to `now` if due — the caller does its real work in the SAME
    transaction as this stamp and commits once, so the anchor only survives
    if the work actually committed too (a crash or raised exception between
    this stamp and the caller's db.commit() rolls BOTH back together — the
    sweep is retried next wake, never silently marked done without running).

    Does not take any lock itself — callers that need one (double-run
    protection across gameserver instances) acquire it BEFORE calling this,
    same as they always did for the sweep's own work."""
    galaxy, last_run = _read_sweep_anchor(db, state_key)
    if galaxy is None:
        return False
    if last_run is not None and (now - last_run).total_seconds() < interval_seconds:
        return False
    state = dict(galaxy.state or {})
    state[state_key] = now.isoformat()
    galaxy.state = state
    flag_modified(galaxy, "state")
    return True



# ---------------------------------------------------------------------------
# Host-loop event broadcast (moved here so every sweep module reaches it
# without depending on core_loop.py)
# ---------------------------------------------------------------------------

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

        # WO-CMB-NPC-INITIATED-1: npc_combat_initiated is BOTH a
        # personal frame to the defender AND a sector spectator broadcast
        # (npc_combat_initiation_service.build_npc_combat_initiated_event's
        # own docstring) — unlike genesis_progress (personal-only) or the
        # generic fallback below (sector-only). Fired from
        # _sweep_one/_maybe_initiate_police_combat (npc_engagement_service.py),
        # which cannot call the live-context emit_npc_combat_initiated (no
        # running loop inside the scheduler's sync per-row SAVEPOINT) — this
        # drains the returned event dict here, POST the per-loop
        # work_db.commit() in _run_due_ticks_sync, same discipline as every
        # other event this function handles.
        if event.get("type") == "npc_combat_initiated":
            defender_user_id = event.get("defender_user_id")
            if defender_user_id is not None:
                try:
                    await connection_manager.send_personal_message(
                        str(defender_user_id), dict(event)
                    )
                except Exception:
                    logger.exception(
                        "NPC scheduler: npc_combat_initiated personal send failed for %s",
                        defender_user_id,
                    )
            sector_id = event.get("sector_id")
            if sector_id is not None:
                try:
                    await connection_manager.broadcast_to_sector(int(sector_id), dict(event))
                except Exception:
                    logger.exception(
                        "NPC scheduler: npc_combat_initiated sector broadcast failed"
                    )
            continue

        sector_id = event.get("sector_id")
        if sector_id is None:
            continue
        try:
            await connection_manager.broadcast_to_sector(int(sector_id), dict(event))
        except Exception:
            logger.exception("NPC scheduler: broadcast failed for %s", event.get("type"))
