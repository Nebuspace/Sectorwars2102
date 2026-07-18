"""NPC Scheduler package — the loops/sweeps formerly in one 7,226-line
``src/services/npc_scheduler_service.py`` (WO-QUALITY-techdebt-scheduler-split).

This is a PURE MECHANICAL DECOMPOSITION: every function moved verbatim into
a per-concern submodule below; zero behavior change. The old module path
now re-exports everything from here as a compatibility shim — see
``src/services/npc_scheduler_service.py``.

Submodules:
  _common                  — shared constants, lock keys, canonical clock,
                              resolve_schedule_block, sweep-anchor helpers,
                              _broadcast_events. No intra-package imports.
  npc_tick_loops            — Loop A/B/C: schedule executor, roster
                              maintenance, off-duty rotation + presence
                              reconciliation.
  economy_governance_sweeps — governance medals/elections/policy sweep,
                              genesis completion, planetary lazy-advance,
                              treasury reconciliation, construction advance,
                              economy-metrics snapshot.
  economy_sweeps            — idle income / daily stipend / bounty accrual
                              faucets, port operating costs, station
                              recovery, reclaim-flag sweep, price recompute
                              flush + alert sweep, price-history rollups.
  reputation_team_sweeps    — weekly personal/faction/ARIA decay, sustained
                              reputation drips, team-reputation sweep.
  pirate_npc_sweeps         — suspect auto-clear, pirate-ecosystem tick.
  contract_sweeps           — NPC contract generation + expiry sweeps.
  presence_helpers          — due-ticks dispatch, boot-time repairs (orphan
                              schedules, trader roster seed/bulk-fill, law
                              patrol dispersal, stranded relocate, trader
                              notoriety/missions), retention/citizen-rebake/
                              presence/ARIA-prune sweeps, route-run retention.
  core_loop                 — the host asyncio task: contract-generation
                              task, npc_scheduler_loop, and
                              _npc_scheduler_main_loop's task-spawn/cancel
                              lifecycle.
"""
