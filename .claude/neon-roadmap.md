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
| 5 | **Fleet & drone command** | surface working fleet/drone admin | 🚧 PARTIAL — FleetHealthReport rescued (run 6); drone command + fleet admin surface still unbuilt |
| 6 | **Governor retheme + presentation pass** | P4.1–4.6 display-sensibility batch | ⬜ NOT RUN |
| 7 | **Dead code purge + typecheck gate** | P5.* purge + tsc gate in CI | 🚧 PARTIAL — purge done (runs 6 + 12, ~8,500 lines); **tsc gate not wired** (vite builds don't typecheck; ~78 pre-existing tsc errors ship silently) |
| 8 | **Economy levers** (own run) | P2.2 admin economy control panel | ⬜ NOT RUN — needs Max design decisions first |
| 9 | **Scopes** (own run) | P2.1 AdminScopeGrant per ADR-0058 | ⬜ NOT RUN — needs Max decisions first |

## Accumulated backlog (parking lots, runs 6–12) — candidates for future runs

### Gameplay-meaty
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
- **player-client WebSocket dead on stage** (surfaced 2026-06-13, SIGNIFICANT) — react-three-fiber WS connection errors every second; ALL realtime push (ARIA chat, live presence, notifications) is degraded → this is *why* COMMS/viewport had to poll. Diagnose the WS gateway / nginx upgrade path. Likely related to the "ARIA chat reply pipe dead" item above.
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

## How to use
Say `neon` and the run self-selects from this file + fresh discovery. Say `neon <batch name>` to direct a run at a specific row. Rows needing Max decisions are marked — they cannot self-select.
