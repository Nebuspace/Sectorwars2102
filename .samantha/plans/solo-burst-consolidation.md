# SOLO-burst consolidation — commit plan (2026-07-16)

## Goal
Consolidate the uncommitted, live-deployed SOLO-era work (Jul-15 20:00Z → Jul-16 05:00Z, 65 files, +5631/−990) into scoped commits on `feat/expeditions-vista`, then reconcile Heimdall's scp-dirtied tree to commit-tracked state.

## State
- All changes attributed to logged HEADS-UPs (17 features/fixes) + 2 pre-SOLO reviewed WOs (team-delete lockorder; contract-board delivery UX). Nothing foreign in the tree.
- Protocol/docs mods (`.claude/`, `.samantha/references/`, `CLAUDE.md`, gen1 archive) = ORCHESTRATOR's lane — excluded.
- `.neon-proof/`, `.samantha/plans/`, agent memories — not for commit.
- stash@{0} (T3-G GameDashboard.tsx+cockpit.css) stays PARKED until Max's "UI session done".
- HELD: commits blocked pending orchestrator's priority WO on this tree (announced 12:56Z).

## Gates run (pre-commit proof)
- tsc --noEmit: CLEAN ✅
- ruff F821 sweep: CLEAN ✅
- ruff full delta vs HEAD: +12 mechanical (I001/F401/E501/S110 + C901s) → monk-lint fixing mechanical; C901/S311/N812 parked as debt.
- vitest targeted: **44 failures / 5 files** — 37 = single render crash (`toFixed` undefined @ WindshieldTableau.tsx:1823), rest GameDashboard behavioral-contract drift → monk-tests triaging (stale-test vs fixture-gap vs live-reachable bug).

## Commit sequence (gameserver — disjoint files, fine-grained)
1. G-NAV: `nav_service.py`, `routes/nav.py` — plot reason codes + ring-1 multi-hop + MoveOption coords.
2. G-NPC: `presence_helpers.py`, `npc_spawn_service.py`, `npc_movement_service.py`, `test_presence_sweep_lock.py`, `test_capital_fed_presence.py` — NPC presence sweep keeps is_npc, Terran densify, capital fed watch, lock_timeout+SAVEPOINT per NPC. (npc_tick_loops.py NOT here — lands in G-ISP, carries both hooks.)
3. G-MOVE: `movement_service.py` — FOR UPDATE lock_timeout=5s + busy refusal.
4. G-HUB: `routes/sectors.py`, `bang_import_service.py`, `test_bang_import_service.py` — New Earth population-hub invariant + pop≥1M fallback.
5. G-TEAM: `team_service.py`, `test_team_delete_dependency_cleanup.py` — WO-TEAM-DELETE-LOCKORDER+DOUBLECLICK (two-check optimistic pre-check + Team lock ordering).
6. G-ISP: migration `c8e1f2a9b4d7`, `models/player.py`, `models/npc_character.py`, `services/intrasystem_movement_service.py`, `routes/intrasystem.py`, `api.py`, `routes/player.py`, `scheduler/npc_tick_loops.py`, `test_intrasystem_movement.py` — WO-ISP intra-system pose.

## Commit sequence (player-client)
7. P-FLIGHT: contexts (WindshieldFlight, Game, Autopilot), api.ts, intrasystemFlight.ts, tactical/* (Tableau, PlanetPortPair, SolarSystemViewscreen, layouts+css), galaxy/* (3D nav suite, CourseConfirmPopup, nav3dFog), hud/AutopilotHud*, services buses (warpCinematic, teleprinter), GameLayout, GameDashboard.tsx, teleprinter.css + all touched/new tests — the flight-feel campaign (one interleaved body of work; bullet sub-features in message).
8. P-DOCK: spacedock/* + module-grid-interface.css — contract delivery/locker-deposit UX.

## After commits
- Heimdall reconcile: bundle → `git fetch` → verify `git diff FETCH_HEAD` is EMPTY (scp'd content ≡ commits) → `git reset --hard FETCH_HEAD` (no content churn, mtime-only HMR flap). If diff non-empty → STOP, investigate.
- STATUS-DONE with SHAs (push+STATUS one step).
