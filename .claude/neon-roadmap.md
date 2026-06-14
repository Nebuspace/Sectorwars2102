# NEON Roadmap — run tracking

Source of the original nine: `.claude/admin-ui-master-list.md` → "SUGGESTED BATCHES (NEON-sized)"
(8 numbered batches; #8 contained two separate runs). Status updated 2026-06-13 after the
Living-NPC + viewport session. This file is the living tracker — update at every run's N10.

## The original nine (admin-UI master list batches)

| # | Run | Scope | Status |
|---|-----|-------|--------|
| 1 | **Stop the bleeding** | /teams crash, port editor 500s, route shadowing, MFA/audit async, ErrorBoundary | ✅ DONE — runs 5–6 (`a888a54`, `df399ef`) |
| 2 | **Users & Teams admin work** | real users CRUD, team admin actions | ✅ DONE — run 5 (users rewire) + run 6 (teams honest-disable; implement-for-real still open, see backlog) |
| 3 | **Honest lists** | station hardcodes → real fields, pagination, sector locations | ✅ DONE — run 6 section A (`5147620`, `d6ee03e`) |
| 4 | **Security console** | P0.8/P0.9/P3.4 + remaining auth gaps: real security overview page | ⬜ NOT RUN |
| 5 | **Fleet & drone command** | surface working fleet/drone admin | 🚧 PARTIAL — FleetHealthReport rescued (run 6); **Fleet Operations tab added (2026-06-13, `2b7d455`)** surfacing `/admin/fleets/*` (live fleets/stats/battles + guarded intervene), proven live; **drone command UI still unbuilt** (`/admin/drones/*` endpoints confirmed live, no UI yet) |
| 6 | **Governor retheme + presentation pass** | P4.1–4.6 display-sensibility batch | 🚧 PARTIAL — **P4.1 Governor retheme DONE (2026-06-13, `2b7d455`)**: white light-island → dark design tokens, proven at 1440×900 + 1920×1080; also honest-disabled the unwired Diplomacy + Culture write controls. **P4.2–4.6 (Event/Players/Combat/Colonization presentation) still open** |
| 7 | **Dead code purge + typecheck gate** | P5.* purge + tsc gate in CI | 🚧 PARTIAL — purge done (runs 6 + 12, ~8,500 lines); **tsc gate not wired** (vite builds don't typecheck; ~78 pre-existing tsc errors ship silently) |
| 8 | **Economy levers** (own run) | P2.2 admin economy control panel | ⬜ NOT RUN — needs Max design decisions first |
| 9 | **Scopes** (own run) | P2.1 AdminScopeGrant per ADR-0058 | ⬜ NOT RUN — needs Max decisions first |

### Admin-UI NEON run — 2026-06-13 (frontend-only; parallel to the player/gameserver instance)
**Shipped + PROVEN live (`b50992d`, `cef2e82`, `2b7d455` on `feat/living-npc-system`):**
- **Governor retheme** (P4.1) — dark-token retheme, both viewports; honest-disabled Diplomacy (no endpoint) + Culture (owner-scoped, no region owners). Filed `regional-governor-admin-write-scope` → sw2102-docs DECISIONS.
- **Fleet Operations tab** (#5) — surfaced `/admin/fleets/*` (live, honest empty states).
- **Orphan-API kills** — SectorDetail PATCH→PUT (proven, DB delta + old PATCH 405); StationsManager delete `/admin/stations`→`/admin/ports` (deployed, endpoint-confirmed); RoleManagement create/save honest-disabled (no dead writes).

**Parked (this run's parking lot):**
- **StationsManager live-delete proof deferred** — path fix deployed + `DELETE /admin/ports` confirmed 200, but deleting a real station is destructive; prove via a created/disposable station next time.
- **RoleManagement mount GETs** still 404 (`/admin/roles`, `/admin/permissions`) — graceful, cosmetic console noise; drop the GETs or gate behind a flag.
- **Fleet "Active Fleets" label** renders all fleets (no status filter) — relabel or pass a filter (cosmetic; harmless while 0 fleets).
- **Drone command UI** (#5 remainder) — `/admin/drones/*` live, no UI.
- **P4.2–4.6** presentation pass (Event/Players/Combat/Colonization) — not run.
- **P4.8 deep-link bounce to /dashboard** — reproduced live (direct `/admin/<route>` load bounces); auth-init race, not fixed this run.

## Accumulated backlog (parking lots, runs 6–12) — candidates for future runs

### Gameplay-meaty
- ✅ **Viewport celestial polish + COMMS→ship selection (2026-06-13)** (`212055b`, `e6c7396`): cockpit windshield refinement (Phase 0 of Max's larger viewport request — pure presentation, no canon). One `MOTION_SCALE` knob slows all orbital/moon/station/belt/ship-transit rates; star moved `w*0.3`→`w*0.54` (centred) so full orbital ellipses fit, orbit extent now capped on both sides; moons of a planet ride ONE shared tilted plane (coplanar, far side dimmed); new **destroyed-planet/collision debris field** flourish (`celestial_service` emits `debris` last so existing layouts are unchanged — two shattered chunks + tumbling rubble + impact haze); **clicking a COMMS contact spotlights its ship** in the windshield with a bold green pulsing reticle + "◉ SELECTED" label (auto-clears when the ship leaves). **Proven live on stage** (testpilot, sectors 3551 + 1311): centred star + full orbits, debris field rendered, selection reticle on the chosen ship.
  - 📐 **Proposed ADR-0073 — persist sector celestial composition + planet discovery/naming** (sw2102-docs `2b5d585`, status Proposed, awaiting Max): canonize storing star kind / body count / belt / nebula / debris (generate-once-then-stable instead of per-request reseed) + planet `discovered_by` (per ADR-0045) + No Man's Sky naming (`auto_name` default + discoverer `custom_name` rename, per ADR-0044 override pattern). Open questions for Max: storage form, owner-rename, uniqueness scope, destructible planets. **Phases 1–3 (migration, persistence, naming endpoint+UI, render-from-DB) are gated on Max accepting the ADR.**
- ✅ **NPC colonist couriers + science vessels (2026-06-13)** (`11b362b`, new `npc_mission_service.py`): NPC traders now run purposeful circuits — ~40% are COLONIST couriers that load colonists at the population hub and ferry them to under-populated planets (owned OR unclaimed), **really growing the target's population**, carrying the colonists as **lootable cargo** (kill one and you take them); ~15% are SCIENCE vessels surveying uninhabited worlds (flavor). Reuses the trader COMMUTE spine + a new `mission_stop` action; startup repair converts the existing fleet (idempotent). **Proven:** courier loaded 488 colonists at hub → delivered to Alpha Centauri (pop 0→250), cargo 488→238. Live mix 47 commerce / 40 colonist / 15 science. Addresses "what are NPCs actually doing" — they now serve the colonist economy. Follow-up: ✅ **ARIA combat/colony/station/strategic responses fleshed out** (`8631a47`…`fa9197e`) — real player data (rank/drones/fleets, colonies, stations, cross-system position) replacing 'coming soon'; fixed intent routing, advisory permissions, and a chain of latent field bugs (Fleet.player_id→commander_id, is_destroyed→disbanded_at, FleetBattle.status→ended_at).
- ✅ **Trader notoriety / paladin targeting (2026-06-13)**: traders span a scruples axis (notoriety 0–100) — reputable → standard → unscrupulous → notorious; the persona title hints at it (Smuggler ≈ notorious, Merchant Prince ≈ reputable); the viewport colors trader glyphs by tier (green/teal/amber/orange), the orbital popup shows a LAWFUL TARGET vs PROTECTED verdict, and the WPN combat list shows standing badges. Killing a REPUTABLE merchant triggers the canon attack_innocent −100 penalty; unscrupulous/notorious are lawful targets (no penalty), distinct from full pirates (HOSTILE_RAIDER). **Proven:** 41/41/15/7 tier spread; persona coherence (Smuggler avg 88, Merchant Prince 22); combat consequence (reputable kill −100 / unscrupulous kill 0); viewport color ramp live. NO-CANON notoriety numbers → DECISIONS. (`08a050c`, `df1c140`, migration `d4e7b1a9c602`)
- ✅ **Port-assault graceful handling (2026-06-13)**: the WPN combat console no longer offers a station ENGAGE that hard-errors "not authorized" — stations show a disabled "ASSAULT NOT AUTHORIZED" instead (`df1c140`). Closes the discovery-scan PAINFUL.
- ✅ **Living NPC System operational (2026-06-13)**: the Cursor-built scheduler (Loops A/B/C) was broken; repaired & shipped. Advisory-lock freeze fixed (xact-level lock + dedicated lock session, `7afef24`); patrols now MOVE and stay DISPERSED — per-NPC phase stagger + per-NPC cursor traversal + 60s Loop A so no sector (incl. the capital) ever empties (`023297f`, `9f07491`); Loop B respawn + roster seeding self-heal at startup. The galaxy reads alive: ~80 patrollers (marshals/raiders) + ~100 traders moving.
- ✅ **NPC trader economy at scale (2026-06-13)**: ~100 merchant captains (`TRADERS_PER_REGION` 6→34) with varied hulls (Hauler/Freighter/Clipper), persona titles, staggered day-clocks, randomized routes; boot-time bulk-fill (pooled routes, fast); traders buy/sell at stations as real MarketTransactions. Spawn seed cut 25k→3k to avoid a credit faucet (`6edb25b`, `4fe0ed9`, run e `c6e5f75…8f6f33d`).
- ✅ **Combat full-haul loot (2026-06-13)**: winning a fight vs an NPC now transfers ALL its credits + ALL its cargo (capped only by your hold) — previously 0 credits + a random 30–80% of cargo. Loaded merchant captains are now a real prize (`882c2f5`).
- ✅ **ADR-0072 Phase 1 (2026-06-12)**: ARIA course plotting + autopilot + navigator voice, sonnet-built (278360d); exploration-map visit hook wired (was zero rows galaxy-wide). Phase 2 = charts/public layer.
- ✅ **Run 14 (2026-06-12)**: token-refresh unification (the 1-hour session killer), market stock regen + Class 8/9 premiums + precious_metals clamp + bounty single-tier, SpaceDock services de-mock + drone field fix (commits 2ff4eb3…b3cd066)
- **Genesis coherence** (NEXT-RUN HEADLINE candidate) — purchase/deploy tier tables incompatible, success_rate/process_hours theater, GenesisDevice model zero writers, 7/12 planet types can never roll (genesis-devices.md bug marker confirmed)
- **Starter ship combat stats** — hardcoded shields:10/weapons:5 ignores ship_specifications_seeder; proof needs a fresh first-login account (Max: create one, or bless a throwaway)
- **Faction mission completion** — accept works; completion mechanics await `faction-mission-completion` pending decision (sw2102-docs DECISIONS.md)
- **Siege semantics** — three pending decisions filed (`siege-turn-length`, `siege-vulnerability-vs-assault`, terraforming tick blessing); vulnerability is display-only until decided
- **Port assault** — fully coded, deliberately disabled (`player_combat.py` "not yet authorized"); Max scoped military takeover as deferred — needs explicit go-ahead + Station defense scalars
- ✅ **NPC respawn loop (2026-06-13)** — Loop B respawn + startup roster seeding now live; population self-heals after kills (was the one-shot-CLI gap noted here)
- **Pirate hulls** — Captains ship placeholder LIGHT_FREIGHTER stats (canon gap — needs hull table from Max)
- **NPC faction in presence payload** (2026-06-13) — `players_present` carries no archetype, so the viewport color-codes ship glyphs heuristically (a hostile can read green); add `archetype` to presence to make it authoritative
- **Trader sleep staggering** (2026-06-13) — `shift_offset_hours` spreads captains across the day-clock, but the night cycle still parks a chunk; widen the spread if lanes feel quiet
- **TRADERS_PER_REGION=34 NO-CANON** (2026-06-13) — operator-tuned; file a canon decision in sw2102-docs DECISIONS.md
- **Region 1005 trade routes thin** (2026-06-13) — randomized routing fills it now, but the region's station topology is marginal; richer station seeding would help
- **npc_restock_demand has no reader** — demand split written, nothing consumes it
- **3 stale galaxy rows on dev** — worldgen drift; prune
- **Genesis tiers** — price-only theater (success_rate/process_hours never persisted or applied)
- **Premium station classes** — Class-8 +20% / Class-9 +25% multipliers documented (trading.md 🚧) but unapplied
- **Gate-material sourcing** — `gate-material-sourcing` pending decision (WJ 200 cargo can't haul phase materials)
- **Warp gate phase 2 extras** — tolls, access modes, transfer/sale, destruction & salvage, upgrades, get-home tow ritual (all design-only per canon)
- **Quantum supply chain** — shard harvesting + crystal assembly (canon design-only; columns exist)

### Cockpit / player UI
- ✅ **Docked-station presentation revamp (2026-06-13)** (`05008a1`): the station console — not the low-value bay scenery — gets the band. The station-bay windshield **auto-minimizes** ~3.5s after docking (animated flex-basis → thin "DOCKED — <station>" strip + ⤢ EXPAND BAY; manual ▴ MINIMIZE too), handing ~85% to the console; **venue tabs** [🛒 TRADE / 🏛️ PORT OFFICE] replace the single toggle; trade desk de-cluttered (drop redundant title bar, hide lone-port dropdown, compact mode toggle/market header/port bar, resource cards 220→138px). **Proven live** (testpilot @ Commerce Sigma): all 9 commodities visible with **no scroll**, PORT OFFICE tab switches cleanly. Added the **SCROLL LAW** to CLAUDE.md (`2e40821`). NB: dev/stage vite-behind-Cloudflare serves stale CSS until `player-client` restart + hard reload.
- 🚧 **BuildingManager real affordability (2026-06-13)** (`296cf0e`): de-mocked colony building-upgrade affordability — was hardcoded credits=50000 / resources=5000, misleading players. Now uses real `playerState.credits` and mirrors the gameserver gate (credits-only: `1000·(t−c)·(t+c)/2`; resources never charged), shows the real cost + "you have <balance>" + real 1h/level time. Build-verified + matches server logic; **live visual proof PENDING** (Chrome extension disconnected mid-round — reachable at /game/planets → owned colony → Manage Buildings; testpilot owns 1 planet). viewport ships no longer drift aimlessly — each runs a stateless seeded itinerary (cruise to a station/planet → dwell docked/landed → move on), staggered per-ship so some are parked while others cruise. NPC departures are staggered 500ms apart so a batch leaving the same poll doesn't all warp out at once. Proven live: ships cluster/dock at the station + NEW EARTH, then relocate over ~13s.
- ✅ **Sector viewport interactivity (2026-06-13)**: click a planet → animated camera push-in to an orbital closeup (name/type/habitability/population/owner HUD + LAND + SYSTEM-VIEW back, Esc exits); ships in sector rendered as faction-colored clickable chevrons (cyan pilot / blue law / red hostile / green merchant) with contact popups; departing NPCs streak off-screen with a warp trail (`57c9950`, `c1ba0b9`).
- ✅ **Population-center planet UI + Pioneer Office (2026-06-13)**: capital hubs (New Earth) open a dedicated Population Center venue brokering real MigrationContracts (broker N pioneers → load in batches → settle over trips); landing Catch-22 + landability fixed (`020e7e7`, `2463981`, `b536a59`).
- ✅ **MFD cockpit console framework (2026-06-13)**: left sidebar → multi-function displays — CMD/NAV/HGR/TRD/COL/WPN/CRW/SVC softkeys, registry-decoupled lazy pages, per-page error boundaries, versioned localStorage; ARIA bottom strip reworked to free right-screen space (`6e9e3b6`).
- ✅ **COMMS presence freeze fixed (2026-06-13)**: client polls current-sector presence every 5s, so NPC/pilot contacts arrive and depart live instead of showing a frozen roster (`b91f487`, `9f07491`). NB: this is a workaround for the dead stage WebSocket (see Hygiene).
- ✅ **NAV legend overlap fixed (2026-06-13)**: legend moved out of the SVG into a flex strip so it no longer covers clickable warp sectors.
- ✅ **Run 13 (2026-06-12) — cockpit identity pass**: CockpitInstrument shell on all 7 routes, HELM rail, docked/landed viewscreen scenes, ARIA console strip (FAB retired), overflow audit ZERO at 1710×947 + 1440×707 (commits 4461040…ffcc7ed)
- **ARIA chat reply pipe dead** (pre-existing, surfaced run 13) — client sends `aria_chat` to the plain WS endpoint which has no handler ("Unknown WebSocket message type"); the handler lives only in enhanced_websocket_service. Wire the handler or point the client at the enhanced endpoint (touches AI dialogue — Max call)
- **COMMS v2** — manual recipient entry, inbox pagination, sent view (needs backend endpoint), message delete UI
- **Sector-type worldgen variety** — dev galaxy has only STANDARD+NEBULA; BLACK_HOLE/VOID/STAR_CLUSTER/ASTEROID_FIELD viewscreen variants code-complete but unproven (worldgen/BANG change; relates to duplicate-Earths invariant)
- **Page-level spinner branches** — PlanetManager/TeamManager (4 sites each) still remount their own subtrees on their local loading states
- **Orbitron font** — referenced across CRT surfaces, never loaded (Max call: load it or standardize Courier New)
- **CORRIDORS IN SECTOR** — arrival-side gates not listed; admin gate-management surface unbuilt
- **Construction claim ignores custom ship_name**
- **Beacon-phase cancel** exists in API, no UI affordance
- **PLANETARY monitor** intermittently empty on first load (transient)

### Hygiene / infrastructure
- ✅ **player-client WebSocket "dead on stage" — DIAGNOSED + FIXED (2026-06-13)** (`f57b021`): the transport was never broken — authenticated handshake returns 101 at every layer (Cloudflare → nginx → gameserver, all verified). Root cause was a CLIENT token-lifecycle bug: the WS singleton reused the login-time token on every reconnect (never refreshed), and an expired token is rejected pre-accept so the browser saw code 1006 (not 4001) → the auth guard never fired → reconnect-looped forever on a dead token. Fix: `openSocket()` reads the latest token from the apiClient store; on auth-suspect close it refreshes once then reconnects (else emits `session_expired` and stops); `connect()` is idempotent while live. Also **unified the refresh single-flight** (apiClient `runRefresh`/`refreshAccessToken`/`getAccessToken`) shared by the WS + 401 interceptor — closes the "Dual refresh-token locks" hygiene item below. Proven: refresh→new-token→handshake 101 through CF. Same root as the recurring mid-session logouts.
- ✅ **ARIA chat reply pipe — WIRED (2026-06-13)** (`383be2a`,`0a1b39e`,`ebaea88`): the plain WS `aria_chat` now routes to `EnhancedAIService.process_natural_language_query` (the same path /enhanced-ai/chat uses — input sanitization + prompt-injection filtering preserved). Fixing it surfaced two masked bugs in the never-functional ARIA query path: `await get_current_player_id()` on a no-arg placeholder returning None (`await None` crashed every query), and `quota_reset_date default=func.current_date` (a SQL func object bound as a value → asyncpg DataError on assistant creation). Both fixed. Proven: WS `aria_chat`→`aria_response` round-trip ("help" returns the full ARIA intro). NB: combat/colony/station/strategic intents still return "coming soon" placeholders (separate content gap — `_generate_*_response` stubs).
- **Combat loot report overstates cargo** (2026-06-13) — `attack_npc_ship` reports the REQUESTED haul, not the capped-actual, when the attacker hold is full (credits exact; cargo line cosmetic)
- **alembic migration_contract_status drift on dev** (2026-06-13) — enum already exists (psql-created), dev alembic not at head; `alembic stamp` clears the boot-time error
- **tsc gate** (the missing half of batch 7) — wire `tsc --noEmit` into builds/CI; burn down the ~78 pre-existing errors
- **Dual refresh-token locks** (AuthContext + apiClient) uncoordinated — unify
- **Route-collision startup check** (master list C.5) — shadowing is a recurring bug class
- **update_port blind-setattr whitelist** (admin P0.2 remnant)
- **process_terraforming_tick** zero callers — scheduler-or-delete decision
- **/quantum/minimap** caching + GameContext-level minimap state (refetch per remount)
- **AuthContext parallel refresher** — delegate to apiClient's single-flight refresh (run-14 reviewer MED; widened exposure now more traffic uses apiClient)
- **Free planet defenses** — update_defenses charges no credits server-side while the UI prices it (run-14 reviewer find)
- **Bounty collusion faucet** — repeat-killing the same deep-negative-rep accomplice pays the system bounty every kill; rep never restored on collection
- **team_service treasury** — commodity-to-cargo routing design (currently honestly rejected)
- **foundation-sprint e2e dir** fuller audit (one stale spec may remain)
- **Sector sub-50-spacing approach gap** — jump bands can't fine-approach; canon texture, maybe intentional

### Discovery scan 2026-06-13 (autonomous N1 — full output archived; top items)

**Player-client BLOCKERs (mocked / orphan UI shipping as real):**
- **Team Alliances / Missions / Analytics** — orphan APIs; opening those tabs 404-cascades (teams.py has no /alliances, /missions, /analytics, or team-list route). Honest-disable or build the backend.
- Ship **Insurance** + **Maintenance** managers — fully-rendered UIs backed by hardcoded constants, zero API calls, no backend routes. Dead UI on /game/ships.
- Planet **BuildingManager** affordability uses mock credits=50000 / resources=5000 — misleads players on whether they can afford an upgrade.
- **Tactical Planner** — plans saved to localStorage only, never sent to combat (zero game effect).
- Team chat "12 members / 8 online" hardcoded · CommsCrew "AFFILIATION: ACTIVE" with no team name · ARIA actions rendered as raw `JSON.stringify` · ship "3D Preview Coming Soon".

**Gameserver PAINFUL:**
- ✅ **Port-assault dead handler** — clicking ENGAGE on a station hard-errors "not yet authorized" (→ fixed in the 2026-06-13 notoriety run as §2).
- **ARIA NL handlers** (combat / colony / station / strategic) return "coming soon!" placeholder (touches AI dialogue — Max call).
- **AI trading / market / route** — fabricated data (`_get_price_history`→[], price→100.0, profit→1500, sectors→id+1/2/3) feeding /ai/market-analysis + /ai/recommendations.
- **Mines** (limpet / armored) — buyable, credits burned, `player.mines` never read (no detonation/deploy/tracking).
- **Faction missions** — service + UI exist, zero FactionMission seed rows → list always empty.

**Security / integrity:**
- **Stellar Blackjack** — /blackjack/action trusts client-supplied card state (`deck_seed` not re-verified) → exploitable credit faucet. RED-mode candidate.
- **enhanced_ai** `get_current_player_id` falls back to None on import failure → could allow unauthenticated AI access.
- **Ranking promotion** — ranks above Captain have no achievement gate (auto-pass) → top ranks are pure credit-farming.

**Admin-ui dead handlers / orphan APIs:** Emergency Operations · Bulk Operations · Player Asset Manager (assign/remove) — buttons disabled, endpoints absent · Performance Metrics / Predictive Analytics / Data Export — orphan APIs (404) · Security threat-rule toggles + IP blocklist non-functional · Event Management uses 6 native alert()/confirm() dialogs.

**Docs-drift write-back (code AHEAD of doc — flip when verified):** warp-gates source map stale (files exist, pipeline shipped) · special-formations "design-only, no model" false (model committed) · messaging "design-only" false (routes+service shipped) · medals "3 wired" → 12 defined · port-ownership "design-only" false (1,511-line service shipped). **Doc AHEAD of code (real gaps):** faction/personal-rep trade pricing not applied (only rank discount) · shield_recharge_rate seeded but no regen loop · Planet.landing_rights/tax_rate columns don't exist · citadel mid-upgrade cancel unimplemented · forming-planet attackable bug (attack_planet has no formation_status guard).

### NEON 2026-06-13-b (player/gameserver) — ✅ 3 PROVEN (`2e930dd`)
- ✅ **ARIA terminal crash** (`6419fe6`): the flagship ARIA MFD page-faulted ("DISPLAY PROCESSOR HALTED") on *any* query incl. its own "best trade route" chip — the plain-WS `aria_response` frame omits a top-level `timestamp`, so `AriaTerminalPage` sorted on `undefined.localeCompare` → MFD boundary fault, sticky session-wide. Fixed client (default missing timestamp + defensive sort + readable action render) + server (emit the declared timestamp). **Proven live**: terminal renders `YOU>/ARIA>`, no PAGE FAULT, console clean.
- ✅ **Free planet defenses** (`0060a8e`): `DefenseConfiguration` priced upgrades (turrets 500/shields 1000/fighters 2000 cr/unit) + gated on real credits, but server `update_defenses` charged nothing (faucet + lying gate). Server now charges added-units only (player+planet row-locked), 400s on insufficient. **Proven**: +20 turrets → −10,000 cr (psql exact), DEFENSE POWER 30→50; negative gate disables Apply. Per-unit prices NO-CANON → DECISIONS `planetary-defense-unit-pricing`.
- ✅ **Forming-planet attack guard** (`2e930dd`): `attack_planet` + `_execute_planet_assault` reject `formation_status=="forming"` (closed genesis-devices.md bug marker). **Proven**: forming → 400 "still forming" (control "no defenses"); undamaged, no turn-spend; WPN omits forming targets.

**🚨 NEXT-RUN HEADLINE — ARIA backend cluster (surfaced by the crash fix):** ARIA now renders but **cannot answer any query** — `greenlet_spawn has not been called` (async/sync) in `enhanced_ai_service._generate_ai_response` + `_log_conversation`, plus `400 Validation failed for id: ID is required` in the audit/log path. Multi-site async-correctness + model-validation cluster in the ARIA query path. The crash fix turned PAGE-FAULT into graceful error fallbacks; making ARIA actually respond is the next ARIA section.

## How to use
Say `neon` and the run self-selects from this file + fresh discovery. Say `neon <batch name>` to direct a run at a specific row. Rows needing Max decisions are marked — they cannot self-select.
