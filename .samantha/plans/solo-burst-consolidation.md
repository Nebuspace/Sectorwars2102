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

---
## WO-COMMIT-BURST: CLOSED-VERIFIED (SHIP 100%/HIGH/HIGH/HIGH, 2026-07-16T13:22Z)

# WO-TRANCHE-0716 — ✅ CLOSED-SHIP 14:10Z (100%/HIGH/HIGH/HIGH; deployed fe2bf9f3, live-proven)
| Lane | Worker | Files | Status |
|---|---|---|---|
| ① T2-E de-clamp (measure-first, STOP-on-conflict) | monk-declamp | planet-port-pair.css | STOPPED — measured fork (36-44ch: name↔height exclusive; 50ch rename cap live) → ❓ to Max via hub 13:34Z |
| ② T3-H type-scale → em | monk-typescale | mfd/cockpit/statusbar css | ✅ COMMITTED bafc260d (integrate prove 1000/1000) |
| ③ T4-K keyboard sweep | monk-keyboard | 12 files, 15 fixes | ✅ COMMITTED d0124eb3 (Pixel fixes verified: 5/5 overlays covered, outline-pure) |
| ④ ISP-PARITY layout port | monk-parity | intrasystem_layout.py + movement svc + tests | ✅ COMMITTED fe2bf9f3 + DEPLOYED (live pools proof 3/3, 0 NaN; scheduler ticking) |
| ⑤ QUEUE-DEADCSS-DECKFLIGHT (micro) | monk-declamp | cockpit-shell.css | ✅ DONE cb7bd45c (comment-only 13+/0−, verified) |
Post-build: review each critically → Pixel re-gates ③ → Mack attacks ④ → ②/③ serial integrate + single HUD prove → scoped commits (Rule 6) → HEADS-UP deploy (frontend) / window if gameserver restart (④) → STATUS per lane.
WINDOW 2 CLOSED-SHIP 18:24Z (7 commits · 2 migrations · P0 chain self-correcting; a6691c9f·072d55aa·42276432·facb9eb7·bca86afb·572f319d·2f74daeb all live).
WINDOW 3 CLOSED-SHIP 21:29Z (pilot wiring + heal shape + null-fix + canonical-%-space live @ 64550cac; hub browser-verified EXACT off-reference parity).
NIGHT WAVE (Max ruled FULL-FEED 23:20Z — 8 ordered lanes + refill to >=12):
| L1 OVERLAY-CONSISTENCY (Max-veto screenshots) + L3 TRANSFERBTN | monk-keyboard | serial, both write cockpit.css | building |
| L2 DEADCODE-UNMOUNTED | monk-canonical | GalaxyMap+ProductionDashboard | building |
| L5 TRAVEL-ROT-EASE + L6 DIAL-FILL (verify-first vs artifact) | monk-declamp | ssv.css / read-mostly | building |
| L7 PERF-TEST-SPLIT + L8 ESLINT-FLATCONFIG | monk-parity | test file / eslint config | building |
| L4 TYPESCALE-COMPOUND-AUDIT (cockpit.css fixes report-only) | monk-audit | table deliverable | building |
REFILL (BACKLOG Wave-1, verify-first, 07-10 specs): escrow-refund → monk-tests · fleet-coord-wire → monk-fleet · colonist-starvation → monk-colony · multiacct-models → monk-multiacct.
DONE: ERRHANDLER-WS f698ce41 (rides next window) ·  QUEUE-PLTAG-MOVING-STAR → monk-declamp (star+moving tags, 105px live defect) · QUEUE-XPCT-SATURATION-STACK → monk-parity (PARKED under Mack REVISE) · DOCKPROX: Mack FAIL — heading-redirect REVISE running; band-geometry ✅ RULED canonical-%-space (3.7px worst vs 90.45px gate — no lockout); window UNHELD post-REVISE; canonical-%-space = priority client ticket next · WO-T1D-LANEB ✅ b71f2901 deployed (stash dropped as ordered; live screenshot on hub verify) · WO-ISP-DOCKPROX+WS-EMIT → monk-parity (serialized same-file; Mack re-attack then window) · QUEUE-CSSVAR-COCKPIT ✅ 5f30e3fe deployed (4/9 evidence-defined; 5 → Max design call). DONE: QUEUE-CSSVAR-DEFECT ✅ 17704371 deployed.
Queued post-wave: (+QUEUE-ISP-WS-EMIT-THREAD — ADDED by hub; SAME-LANE constraint: serialize behind/with WO-ISP-DOCKPROX, never two workers into that file) · QUEUE-CSSVAR-DEFECT (define/rescope --primary-green + --accent-primary at the correct layer, NOT per-site fallbacks; live defect: invisible hover/selected borders). Then: QUEUE-DEADCODE-UNMOUNTED (GalaxyMap+ProductionDashboard+tests, one commit, bundle-delta) · QUEUE-DEADCSS-TRANSFERBTN (plain removal, verify-first incl. dynamic template strings) · QUEUE-TYPESCALE-COMPOUND-AUDIT (declaration→ancestor-chain→factor table, rendered-proof standard) (delete GalaxyMap.tsx+test, verify-first, bundle-delta) · QUEUE-UX-OVERLAY-CONSISTENCY (RULED A: PriorityHail visible-dismiss on all 5 overlays + keel auto-dismiss; supersedes KEELCEREMONY ticket; Accept = pattern-parity table + Pixel gate + 1440×900 before/after screenshots for Max veto).
Standing holds: team_service pair (Max-gated) · stash@{0} T3-G (Max's word) · stash@{1}.
