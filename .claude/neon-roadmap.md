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
| 5 | **Fleet & drone command** | surface working fleet/drone admin | ✅ DONE — Fleet Operations tab (`2b7d455`) + **Drone Operations section in CombatOverview (run 2, `bc8a1e4`)** surfacing `/admin/drones/*` (stats + active/destroyed lists + force-recall/restore), proven live |
| 6 | **Governor retheme + presentation pass** | P4.1–4.6 display-sensibility batch | 🚧 PARTIAL — P4.1 Governor retheme DONE (`2b7d455`); P4.2 Event de-purpled (`007c3ff`); **P4.6 Combat red-zero card neutralized (run 3, `c6bb218`)**; **P4.7 native-dialog sweep COMPLETE (run 2 toast system `7218b5b` + run 3 sweep `48b13cf` — zero native dialogs remain admin-wide).** **P4.3 Players white-banner fixed (run 7, `bdb4e67`)**; P4.5 Colonization done (run 4). Still open: P4.4 Galaxy Map (renders dark in-browser → GOLD token-hygiene, not a visible bug) |
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

### Admin-UI NEON run 2 — 2026-06-13 (frontend-only)
**Shipped + PROVEN live (`7218b5b` → `bc8a1e4`):**
- **Toast + typed-confirm system** (`contexts/ToastContext.tsx`, `useToast`/`useConfirm`, dark-token, light-mode-safe) — retires the native `alert()/confirm()` freeze-trap class. Proven: StationsManager delete → in-shell danger ConfirmDialog, Cancel blocks the delete.
- **Event Management** de-purpled (P4.2) + toast retrofit. Proven on `:9081` (stage host serves stale Cloudflare CSS until purge).
- **Drone Operations** section in CombatOverview (#5 done) — live `/admin/drones/*`, honest empty states.
- Adversarial review caught + fixed a HIGH (toast light-mode unreadability) and a MED (concurrent-confirm promise leak) pre-deploy.

**Parked:** remaining P4.7 native-dialog offenders (WarpTunnels 5, FleetManagement 5, PlayerDetailEditor 5, SectorDetail 3, …); drone status-badge color variants (returning/damaged default green); event-management dead CSS blocks; **Cloudflare stale-CSS** on the stage host (purge or wait for expiry to see the de-purple there).

### Admin-UI NEON run 3 — 2026-06-13 (frontend-only)
**Shipped + PROVEN live (`48b13cf` → `f670722`):**
- **Native-dialog sweep COMPLETE** (P4.7) — WarpTunnels/FleetManagement/PlayerDetailEditor/SectorDetail/SectorEditModal → toast/confirm; **zero native `alert()/confirm()` remain admin-wide** (build-grep verified). The freeze-trap dialog class is gone.
- **Combat Balance Analytics** (ZONE-C-07) — new section in CombatOverview surfacing `/admin/combat/balance` + `/stats` (group-by, win-rate spread, outliers, recommendations); filters the synthetic `all_players` aggregate. Proven live (200).
- **P4.6** — ACTIVE BATTLES=0 card neutralized (alarm-red only when intervention needed). Proven.
- **Dashboard Recent Audit Events** (D-02) — `/admin/audit/logs` surfaced on the operator hub. Proven live (200).
- Review caught + fixed a MED (all_players aggregate rendered as a peer rank row).

**Parked:** P4.3 Players page, P4.4 Galaxy Map, P4.5 Colonization (presentation); pre-existing tsc errors (PlayerDetailEditor `current_region_id`, AITradingDashboard ws-events — not introduced this run); Dashboard audit-feed inline styles → move to CSS classes; audit-log entries show actor `anonymous` for the read-endpoints (backend data-quality note).

### Admin-UI NEON run 4 — 2026-06-13 (frontend-only)
**Shipped + PROVEN live (`951cbda` → `dc6b598`):**
- **Colonization overview honesty** (P1.3/P4.5/B-05) — owned-colony filter: TOTAL COLONIES **1490→3**, population **8B→200** (DB: 3 owned of 1491 planets); deduped doubled planet name; unit labels on mixed-unit resource bars. Proven live.
- **Economy Health snapshot** (D-05) — `/admin/economy/dashboard-summary` surfaced on the Economy page (gini/velocity/M2/GDP/alerts); labels disambiguated from the legacy grid (review MED fix). Proven live (200).
- **CentralNexusManager dark retheme** (B-02) — light CSS fallbacks → dark tokens. Proven dark live.

**Parked:** ColonyOverview status is now always 'active' after the owned filter (active/total redundant, abandoned/developing/troubled filter options unreachable) — derive status from the real `planet.status` field next time; ColonyOverview `morale` derived from `habitability_score` (0–1) treated as 0–100 (pre-existing unit bug); Colonization page heading/tabs still indigo (separate chrome); DashboardSummary `critical_alerts`/`top_trading_ports` interface fields unused/inaccurately-typed (trim).

### Admin-UI NEON run 5 — 2026-06-14 (frontend-only)
**Shipped + PROVEN live (`2665231` → `89a2505`):** three brand-new admin pages surfacing previously-unsurfaced subsystems (the P0.4 async breakage that killed factions/messages is fixed upstream):
- **Faction Management** (`/admin/factions`, #3.5/ZONE-C-03) — faction list + active missions + stats. Proven (Terran Federation, Courier Run mission).
- **Message Moderation** (`/admin/messages`, #3.5/ZONE-C-04) — flagged queue + stats + guarded moderate (clear-flag/delete). Proven (8 total / 0 flagged, 2 senders).
- **Translation Management** (`/i18n/admin`, #3.3/ZONE-C-05) — language coverage overview + per-language progress. Proven (12 languages, English 100%).
All routed + sidebar-wired (Factions→Game Operations; Message Moderation + Translations→Security & Admin). Review fixed 2 MEDs (undefined `--status-danger`→`--status-error`; over-promising "Flag Reason"→"Status"). Deferred my dev-deploy + proofs around the other instance's gameserver DEPLOY WINDOW (blackjack-faucet round) per protocol.

**Parked:** Faction/Message/Translation pages are read-mostly v1 — faction create/edit/delete + translation key-editing endpoints exist but weren't wired (guarded-mutation follow-up); Message Moderation "Most Active Senders" shows raw player UUIDs (backend returns no nickname); the destructive red Delete button is unprovable-in-browser until a flagged message exists.

### Admin-UI NEON run 6 — 2026-06-14 (frontend-only; self-selectable set)
**Shipped + PROVEN live on dev (`d66f954`, `f2d8dee`):** 2 sections; discovery dropped 2 stale candidates (orphan-handler honest-disable + Players dark-pin were already done) and 1 over-claimed candidate (Bang Galaxy "light-islands" render dark in-browser → GOLD token-hygiene, not a visible NEON win).
- **Security console honesty** (`SecurityDashboard.tsx`, #4) — the Threat Detection rule toggles, the IP-blocklist add form, and the Security-Policies Edit buttons were fully interactive but wired to nothing (no handlers, no endpoints). Disabled them all + added honest amber `role="note"` banners (matching the EmergencyOperationsPanel idiom). Wired Overview/Alerts/Audit surfaces + the real Enable-MFA button untouched. **Proven:** amber notes render; Suspicious-Pattern toggle stays OFF on click (disabled); IP input+button + both policy Edit buttons disabled; zero console errors.
- **ColonyOverview data correctness** (`ColonyOverview.tsx`) — `habitability_score` is stored **0–100** (DB: Aquarius 52 / Gamma 25 / Pollux 28) but the Habitability tile did `*100` then capped, so every colony showed **100%**; per-card status was always 'active' while the summary counted "troubled" (morale<50). Dropped the `*100`; derived status from morale. **Proven:** Aquarius tile 100%→**52%** (DB-matched); badges now Aquarius ACTIVE / Gamma+Pollux TROUBLED; summary fixed from the impossible TOTAL 3 / ACTIVE 3 / TROUBLED 2 → ACTIVE 1 / TROUBLED 2. `resource_richness` (genuinely 0–1) correctly kept its `*100`. Adversarial review (2 reviewers) PASS, no CRITICAL/HIGH.

**Parked (run 6 finds):**
- **Players page white banner (P4.3)** — STRONG next-run candidate: the `.section-header` "📊 Player Metrics" region renders as a **white island** (title white-on-white, only the subtitle visible) + 2 saturated solid-blue/orange stat cards among 4 dark ones. Root cause is in **shared** `styles/layouts.css` `.section`/`.section-header` (global blast radius) — needs careful page-scoped pinning, not a tail-of-run rush. (The code-only scanner wrongly called this page "compliant"; the browser disproved it.)
- **`.btn:disabled` not visually dimmed** — disabled `.btn-primary` (e.g. IP-blocklist Add) still looks blue/clickable (functionally inert + title, weak affordance); a global `.btn:disabled { opacity }` would improve every honest-disabled primary button. LOW.
- **Bang Galaxy CSS** uses hardcoded hex fallbacks instead of design-system tokens (renders dark, no visible defect) → GOLD token-hygiene pass.
- **ColonyOverview** `|| 0` (water) vs `|| 50` (morale/status) fallback skew for a genuine 0 score; redundant `Math.min` in status ternary — LOW polish.

### Admin-UI NEON run 7 — 2026-06-14 (frontend-only)
**Shipped + PROVEN live on dev (`bdb4e67`, `1ee2d3e`, `0442ca8`):** 3 sections.
- **Players white banner (P4.3) — ROOT-CAUSED & FIXED** (`player-asset-manager.css`). NOT the shared `layouts.css` as run 6 guessed: `player-asset-manager.css` declared **unscoped global** selectors, and because `PlayerAnalytics.tsx` imports `PlayerAssetManager`, that CSS is bundled into the Players page chunk (confirmed: `#f8f9fa` present in `dist/.../PlayerAnalytics-*.css`). Its global `.section-header { background:#f8f9fa }` leaked onto the shared `.section-header`, painting the "📊 Player Metrics" header near-white and hiding the near-white title. Scoped the 3 `.section-header` rules to `.player-asset-manager`. **Proven:** header now dark, title legible (was a white slab). No Chrome needed — solved via the build output.
- **RoleManagement 404 noise** (`RoleManagement.tsx`) — stopped the mount-time `/admin/roles` + `/admin/permissions` fetches (design-only per ADR-0027/0058, they 404'd every visit). Now shows the existing design-only banner with **no** misleading red error box. **Proven:** Permissions page renders the amber design-only banner, zero console messages/404s.
- **Fleet "Active Fleets" honest label** (`FleetOperationsTab.tsx`) — the panel listing ALL fleets (with a Status column) was titled "Active Fleets"; relabeled "🛰️ Fleets" / "No fleets." The separate stat card that genuinely uses `stats.active_fleets` is untouched. **Proven:** DOM shows "🛰️ Fleets" + stat card intact. Adversarial review PASS (no CRITICAL/HIGH); fixed 2 LOWs pre-deploy (removed misleading red error box, dropped await-less async, stale comment).

**Parked (run 7 finds):**
- **player-asset-manager.css global-CSS pollution (bigger fish)** — beyond `.section-header`, this file still has ~30 other UNSCOPED top-level selectors bundled into the Players chunk that collide with globals: `.btn`, `.btn-primary/danger/secondary/outline`, `.btn:disabled`, `.tab`, `.manager-header`, `.section-actions`, `.asset-list`, `.empty-state`, `.close-btn`, etc. The generic ones could leak onto other pages like `.section-header` did. Follow-up: scope the WHOLE file under `.player-asset-manager` (deferred — needs careful verification that all PAM markup is under its root; it is, but a 30-selector edit warrants its own pass). **This is a systemic pattern** — many component CSS files (~25 define their own `.btn`/`.btn-primary`) are unscoped; a broader CSS-isolation sweep (or CSS modules) is the real fix.
- Players saturated blue/orange stat cards (`data-variant="primary"`/`warning`) — intentional design-system variants, left as-is (not a bug).

### Admin-UI fresh audit + NEON run 8 — 2026-06-14 (frontend-only)
Ran a 4-agent fresh audit (dead code / mock data / orphan APIs / spec-deviation). Headlines:
**0 orphan components, 0 mock-data-presented-as-real** (dashboards are honesty-first; Math.random metrics confirmed gone). Real issues are RBAC/spec drift + a few live orphan/crash bugs. Then shipped 2 honesty sections:
- **§A planet editing honest-disabled** (`PlanetDetail.tsx`, `PlanetDetailModal.tsx`, `1bc49f8`) — both fired `PATCH/PUT /api/v1/admin/planets/{id}` (no such endpoint) → 404 behind a misleading "Failed to update/save". No backend to wire, so planets are now **view-only**: inline fields read-only, modal ignores edit mode + "Edit Planet (unavailable)" disabled, save paths neutered. **PROVEN live:** clicking the planet edit ✏️ opens the modal in **view mode** (read-only fields/badges), footer button disabled w/ honest title, no 404.
- **§B colonization dead-button honest notes** (`ColonyOverview/PlanetaryManagement/GenesisDeviceTracking.tsx`, `1ca3337`) — the detail-modal "Actions" rows were no-op buttons (no onClick); replaced each with an honest "not yet available" note. **Built + tsc/build/adversarial-review verified; NOT browser-proven this run** — colonization data was empty (0 colonies/planets in those tabs at prove time), so the detail modals couldn't be opened to screenshot. Re-prove when colonization has data.
Adversarial review PASS (no CRITICAL/HIGH/MED). Verdict STEADY (§A proven, §B verified-not-live-proven).

**Parked — audit findings NOT yet actioned (for Max / future runs):**
- 🔴 **RBAC: 19-scope `AdminScopeGrant` model (ADR-0058) is absent** — admin-ui is still flat `is_admin` (~19 gates in AdminContext); no `/admin/scopes` grant UI (PermissionsDashboard is an unrelated design-only *roles* concept), no `/admin/review-queue` (the accountability control), no dedicated `/admin/audit` route, no scope-aware 403. This is roadmap #9 (needs Max + backend). Biggest gap.
- 🟠 **CombatFeed.tsx latent crash** — interface declares flat fields but JSX reads nested (`event.result.*`, `event.attacker.name`) → `undefined.toUpperCase()` when combat events exist. Needs interface/render reconciliation with the real payload (and the backend combat-event shape).
- 🟠 **LoginForm debug/direct-login harness in prod** ("Test Direct API" button + `testDirectLogin` writing tokens to localStorage) — AUTH code → NEON out-of-bounds; left for Max to remove/gate.
- 🟠 **Governance writes hit global `/regions/my-region/*`, not the selected region id** — contradicts the blessed admin-by-region-id canon; real fix needs the admin-by-region-id endpoints (open DECISIONS lineage).
- 🟡 Dead handlers still live: AdvancedAnalytics per-report Download/Share, RegionalGovernor treaty "View Details", PlayerBehaviorAnalytics `setProfiles([])` demo-stub, RouteOptimizationDisplay unused mapRef.
- 🟡 Backendless-but-honest `reports/*` trio (CustomReportBuilder/AdvancedAnalytics); dead `addSectors` in AdminContext; ~500-line unused `types/admin.ts`; 4 unused barrel `index.ts`; 3 orphan CSS; `PlayerAssetManager` ships filter uses camelCase `ownerId` backend likely ignores.
- 🟡 Doc drift: admin-ui.md page table lists ~6 pages that don't exist + omits 4 that do (BangGalaxy/Factions/Messages/Translations) — fixable in sw2102-docs.
- Culture/Diplomacy tabs aren't "honestly disabled" per spec (Diplomacy loads a real `/treaties` table) — but the endpoint exists, so the spec note may be stale; needs a Max call.

### Admin-UI NEON run 9 — 2026-06-14 (frontend-only)
**Shipped to dev (`6c07b06`):** fixed the audit's 🟠 **CombatFeed latent crash**, which turned out to be a 2-component cluster. `GET /api/v1/admin/combat/live` returns the backend **CombatFeedItem** (started_at, duration_seconds, victor_id, combat_stats dict, attacker/defender objects {id,type,name,level}, sector dict), but BOTH `CombatFeed.tsx` AND `charts/CombatActivityChart.tsx` read a non-existent nested shape (`event.result.winner`, `event.result.damageDealt`, `event.timestamp`, `event.attacker.ship`) → `undefined.winner`/`undefined.toLocaleString()` threw at render, crashing the whole Combat admin page whenever any combat exists (masked only because dev has 0 live combat). Reconciled both to the real payload, defensively (winner from victor_id; started_at; duration_seconds; participants by name/type/level; empty state; dropped the guessed combat_stats damage/loot/ships + sector coordinates rather than invent dict keys; event props loosened to `any[]` at the boundary). tsc on the file went 4 errors → 2 (the 2 remaining are pre-existing d3 axis-overload typings). **Build + adversarial-review verified (PASS, no CRITICAL/HIGH); NOT browser-proven** — 0 live combat on dev, and the crash only triggers with events. Re-prove when there's a real fight.
**Follow-ups:** (1) `combat_stats` dict keys are unconfirmed — the chart's damage series reads `combat_stats?.damageDealt ?? 0` so it's crash-safe but may render flat-zero until the real keys are confirmed (ask the gameserver lane); the feed's damage/loot/ships display was dropped pending the same. (2) `CombatOverview.tsx` still casts the live response `as CombatEvent[]` with its own historically-flat local type — harmless now (children read defensively) but a shared `CombatFeedItem` type + dropping the cast would be cleaner. (3) the CombatFeed "Dispute" button has no backing dispute concept in the new payload — drop or wire.

### Admin-UI NEON run 10 — 2026-06-14 (frontend-only; dead-code purge, #7)
**Shipped (`86f4e5d`):** removed 8 confirmed-dead admin-ui files (zero importers/references, verified by grep; vite build stays green; no behavior change → no restart/live-proof needed): `types/admin.ts` (~500 lines nothing imports), 4 unused barrel `index.ts` (ai/analytics/permissions/security), 3 orphan CSS (comprehensive-admin-dashboard / universe / universe-enhanced). Progresses roadmap #7 (purge half).
**Still parked (lower-confidence / live-file edits, deferred):** unused EXPORTS inside live files — `utils/auth.ts` token helpers (decodeToken/isTokenExpired/getTokenTimeRemaining), `services/websocket.ts` camelCase aliases, `AdminContext.addSectors` dead method; these need surgical edits to in-use files (vs whole-file deletes) so left for a focused pass. The **tsc gate** half of #7 (wire `tsc --noEmit` into CI + burn down the ~76 pre-existing errors) remains the bigger open piece.

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

**ARIA backend cluster (surfaced by the crash fix):**
- ✅ **Validation half FIXED (2026-06-13, `0c1ff49`, loop iteration):** the `400 Validation failed for id` was `ConversationContext(assistant_id="")` failing `__post_init__` validation on every threaded query (WS `handle_aria_chat` + REST `/enhanced-ai/chat`). Now the service builds the context with the real `assistant.id`. **Proven live**: "analyze my combat readiness" → real data (Recruit/0/0, psql-exact), "help" → full intro, threaded 2nd query works; non-trading intents answer; logs clean.
- ✅ **Greenlet half FIXED (2026-06-13, `80e80da`):** the trading `greenlet_spawn` was an expired-ORM lazy-reload — the recommendation path commits mid-request (`expire_on_commit=True`), expiring `assistant`, so a later `assistant.security_level` (enhanced_ai_service:513) + `assistant.id` (_log_conversation) sync-reloaded on the async session. Fix: capture those fields before the commit; `_log_conversation` takes `assistant_id`; security_level audit field made enum-or-str robust. **Proven live**: the "best trade route" chip (page-faulted 2 rounds ago) now returns real recommendations; combat answers too; clean logs. **ARIA backend cluster fully resolved.**
- ✅ **ARIA trade numbers de-mocked (2026-06-13, `31a6cb1`):** `ai_trading_service` recommendation engine wired to real data — `_get_price_history`→enhanced_market_transactions, `_get_current_market_price`→market_prices, `_find_best_simple_route`→real cross-sector arbitrage, `_get_nearby_sectors`→real sector_warps BFS, `_get_active_predictions`→ai_market_predictions. **Proven live**: /ai/recommendations 1500/sector-1305 → real exotic_technology 1302(150)→1314(275) profit 12,500 (DB-confirmed executable); /ai/market-analysis organics 0.0→8.0/falling. Adversarial review caught + fixed an inverted price direction. Canon divergence (global vs explored-sectors) → DECISIONS `aria-trade-routes-global-vs-explored`.
- ✅ **ARIA flavor stubs de-mocked (2026-06-13, `7e659d7`):** `_identify_price_factors` (static list → real demand/supply skew + price spread + 7-day volume) and `_assess_sector_risk` (hardcoded 0.3 → live HOSTILE_RAIDER count per sector). **Proven live**: organics factors real; a 2-raider sector returns a real AVOID warning (risk 0.80, DB-confirmed). 
- 🚧 **Remaining ARIA items:** combat/colony/station/strategic content depth is a Max-call on AI dialogue; `expire_on_commit=False` on AsyncSessionLocal would systemically kill the greenlet class (wants adversarial review); full canon: gate routes by ARIAExplorationMap + personal observations (DECISIONS `aria-trade-routes-global-vs-explored`).

### NEON 2026-06-13-e (player-client) — ✅ COMMS/CREW team name (`3ccd063`)
- ✅ **Cockpit COMMS/CREW shows the real team name** (`3ccd063`): was hard-wired "AFFILIATION: ACTIVE" for any team member; now CommsCrewPage fetches teamAPI.getTeam(team_id) and shows "[TAG] Name". Proven live (testpilot in Neon Raiders → "[NEON] Neon Raiders").
- 🧹 **Dead-component purge candidate (GOLD / Max delete-decision):** a player-client mock-sweep confirmed these are exported but NEVER mounted (not player-visible, but carry fabricated data — a trap if revived): `ShipDetails.tsx` (mock modifications, fake history, 3D-preview stub, baseValue=50000, drone self-ref), `TeamChat.tsx` (12 members/8 online), `TeamAnalytics.tsx` (Math.random heatmap, hardcoded +23%/+5% badges, #1/#2/#3), `FleetCoordination.tsx` (50000/ship), `MaintenanceManager.tsx` (85/92/78), `AllianceManager`/`DiplomacyInterface` (hardcoded 2102 timeline, getTreaties missing) + their orphan team APIs (alliances/missions/analytics/transfer 404). Either delete or wire; not browser-provable so out of NEON scope — flag for GOLD or Max.

### NEON 2026-06-13-f (gameserver) — ✅ mines honest-disabled (`a28a734`)
- ✅ **Armory no longer sells no-op mines** (`a28a734`): limpet/armored mines were buyable but `player.mines` is never read (no deploy/detonate path) — a credit sink. Catalog flags them `available=false` ("Mine deployment is not yet available"; client already renders disabled + reason) + purchase endpoint 400s. Proven live (catalog flag, purchase 400, credits unchanged). Real mine deployment remains a future feature (effort 5+).

### NEON 2026-06-14-g (gameserver, RED) — ✅ Blackjack credit faucet closed (`b0b988d`)
- ✅ **Stellar Blackjack credit faucet CLOSED** (`b0b988d`): /blackjack/action trusted client cards + was stateless (fabricate a win / no-deal / replay → mint credits). Now server-authoritative — active game in Player.settings, hands rebuilt from the server seed, payout from the stored bet, cleared on settle. **Proven live** (no-deal→400, fabricated cards/bet ignored, replay→400, DB reconciles). Adversarial review caught 2 CRITICALs pre-deploy (locks needed populate_existing; double-into-bust paid double). Closes the discovery-scan RED candidate.

### NEON 2026-06-14-h (gameserver) — ✅ gambling concurrency locks (`9a91895`)
- ✅ **Gambling credit settlement concurrency-safe** (`9a91895`): audit follow-up to the blackjack faucet — slots/dice/lottery were server-authoritative (no fabrication) but didn't row-lock the player, so concurrent gambling could lost-update credits. All three now use the same populate_existing()+with_for_update() lock as blackjack. **Proven live**: 10 concurrent slot spins → DB credits = before + Σ(net) exactly (no lost update). Gambling surface fully hardened.

### NEON 2026-06-14-i (gameserver) — ✅ move encounters surfaced (`478ec80`)
- ✅ **/player/move surfaces entry encounters + tunnel events** (`478ec80`): MovementService generated them + MoveResponse declared the fields, but the route never copied them → response_model stripped them (encounters hidden, ARIA autopilot pause blinded, ADR-0072). One-line forward. Proven live (move into NEBULA 1319 → sector_hazard encounter in response).

### Fresh discovery queue (2026-06-14 full scan — BUILDABLE, in-lane, provable, NOT Max-gated)
- ✅ **/drones/sector/{id} auth FIXED (2026-06-14, `f558e98`)** — was unauthenticated (anonymous drone-presence enumeration); now requires get_current_player. Proven: unauth→401, auth→200.
- ✅ **ColonySpecialization UI honesty FIXED (2026-06-14, `945668d`)** — benefits now show the real applied production deltas incl. penalties; dead defense/research badges removed; balanced honestly baseline. Proven live. Backend gap (defense/research multipliers unapplied) → DECISIONS colony-specialization-defense-research-unapplied.
- ✅ **AI market predicted_price fallback FIXED (2026-06-14, `ce77c8c`)** — engine-failure fallback now returns the flat last price (no fabricated +5%). Proven via diag (None/raise → 120.0 not 126.0).
- ✅ **/combat/engage port -> 501 FIXED (2026-06-14, `fa05b38`)** — disabled-feature path now returns 501 Not Implemented (was 200 error body). Proven.
- ✅ **apiRequest error messages FIXED (2026-06-14, `c0fbcf8`)** — now detail||message; real server messages surface app-wide. Proven (501→"Port assault...", 404→"Target ship not found", were "API Error: code").
- 🟡 **First-login ShipSelection JSON dump FIXED build-verified (2026-06-14, `27fc2fe`)** — raw JSON.stringify + session-state debug replaced with a friendly empty-ships message. tsc-clean, error-branch-only. LIVE-PROOF DEFERRED: only renders during first-login (empty-ships branch); not reproducible without a fresh first-login account (account creation out of bounds). Re-prove if Max provides a throwaway first-login account.
- 🟢 **/drones/deploy random deploymentId** (drones.py:508) — returned id not tied to a DB row → recall 404s (note: DroneManager UI unmounted, so API-only).
- ⛔ Max-gated/large: drone create/upgrade/repair costs (no canon + UI unmounted), faction-mission completion, faction pricing-modifier stacking order, pirate hull stats, enhanced-WS trading stubs (client never connects), GOLD purge of dead components.

### GOLD purge — ABORTED 2026-06-14 (canon check: these are PLANNED scaffolding, NOT dead code)
Max chose the purge but added "be sure it is not a planned feature in sw2102-docs." Canon check result: the "dead/unmounted mock components" are almost all **scaffolding for designed-but-unbuilt features** — DO NOT DELETE:
- **InsuranceManager** → ships.md + ship-insurance.md (planned). **MaintenanceManager** → ships.md "Maintenance system" 🚧 Partial. **FleetCoordination** → fleet-tactics.md (formations ✅ shipped; coordination bonuses). **TeamChat** → messaging.md (team broadcasts / team channel). **AllianceManager + DiplomacyInterface** → factions-and-teams.md "Alliances (design only)". **ResourceSharing** → factions-and-teams.md Treasury (transfer between members). **ShipDetails / TacticalPlanner / MissionPlanner** → ships/fleet-tactics adjacent (ambiguous → keep).
- The mock data inside these is an "if-mounted" trap, not abandoned junk. Correct disposition: **build mock→real when each feature is scheduled** (most are the Max-gated items above), not delete.
- **TeamAnalytics** is the ONLY candidate with no canon backing (no team-analytics/heatmap/perf-metric doc). Borderline — confirm with Max whether to document-as-planned or delete; not deleted unilaterally.
- Nothing was deleted. The earlier "GOLD purge candidate" framing was wrong — these are unbuilt-feature scaffolding.

## How to use
Say `neon` and the run self-selects from this file + fresh discovery. Say `neon <batch name>` to direct a run at a specific row. Rows needing Max decisions are marked — they cannot self-select.

### NEON BUILD round — 2026-06-14 (player-client; flip from GOLD purge to build)
Max redirected the aborted GOLD purge into a **build**: the "dead" team scaffolds
are canon-backed planned features, so flesh them into real code instead of deleting.
Selected 2 (both backends already implemented → wire + mount, no migration):
- **Team Treasury** (ResourceSharing) — ✅ DONE/PROVEN (`f3e249e`+`bcbdd50`). Real
  deposit/withdraw/transfer + balance vs the live treasury; credits+quantum_crystals
  only (server whitelist); withdraw/transfer gated on can_manage_treasury; mounted in
  the Treasury tab. psql-exact deposit/withdraw/transfer proof as verifpilot.
- **Team Chat** (TeamChat) — ✅ DONE/PROVEN. Real send/read (fixed response-shape +
  missing subject), real member count, new Chat tab; message persists (psql).

**Deferred (bigger / Max-gated):** insurance + maintenance (need gameserver
mechanics; premium % still 'proposed'), fleet-coordination UI (supply/coord-bonus/
non-flagship roles design-only), alliances/diplomacy (design-only, no schema),
treasury transaction-history (needs a migration), TeamAnalytics (no backend).

### NEON BUILD — 2026-06-14 — Ship Maintenance v1 (scaffold-build series, 3rd)
✅ DONE/PROVEN (f080ce6). Lazy condition decay (canon per-hull rates, advance-on-read,
no migration) + performance-band COMBAT penalty consumed in combat + tiered shipyard
servicing (GET/POST /ships/{id}/maintenance). MaintenanceManager rebuilt to canon +
mounted in SpaceDock Ship Services; VesselPage reads real `condition`. Proven: Scout
decay 100→35 @65d (band exact); combat attack 26.25/20.0/6.25 across bands; basic
service 35→100 for 325cr. ships.md status flipped.
**Deferred (own round):** per-jump failure roll (Minor/Major/Catastrophic — catastrophic
touches the destruction handler), speed/fuel band consumption, repair timers, self-repair
Maintenance Kit. maintenance_rate seed is dead/mismatched (cleanup).
