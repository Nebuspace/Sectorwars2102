# Admin UI Master List — fix / improve / enhance / fill-in vs spec

Audit date: 2026-06-11 · Sources: full live browser sweep of all 22 routed pages (as real admin),
canon spec audit (OPERATIONS/admin-ui.md, ui-flows.md, DATA_MODELS/admin.md, ADR-0027/0058),
frontend inventory (24 routed entries + 13 dead components), backend inventory (22 route files,
~190 admin endpoints). Severity: P0 broken-now → P5 cleanup. Sizes S/M/L.

**Run-6 completions (neon-2026-06-11-f, LEAN tier):** P1.1 (stations real owner/fee/tax/security; sector richness; sector-port tax), P1.2 (server pagination + honest totals on stations & sectors, plus server-side stations search added at gate), P1.9 (sectors Location resolves region/cluster), P5.1–5.3 (13 dead components + 4 css deleted, FleetHealthReport rescued into FleetManagement wired to /admin/ships/health-report, UniverseManager dead branch removed), and P0.7-adjacent wire-ups: GET /admin/combat/logs implemented (feeds Rankings), DisputePanel resolve fixed (path/id/body), WarpTunnels PATCH→PUT proven with DB delta, honest-disable on Teams admin actions / PlanetsManager delete / player bulk+emergency+asset ops. NEW FINDS: StationsManager PortModal edit form was half dead-write (non-column fields now read-only; `update_port` blind-setattr should whitelist — P0.2 remnant), warp tunnel `max_ship_size` has no backend column (read-only'd), stale `test_nexus_endpoints.py` Port import had been interrupting ALL pytest collection (repaired — 407 passed vs 318 baseline).

---

## P0 — BROKEN NOW (admins hit these today)

| # | Item | Detail | Size |
|---|------|--------|------|
| 0.1 | **/teams page crashes to blank screen** | `TeamManagement.tsx:580` derefs undefined `.toLocaleString` — triggered by the first real team (Neon Raiders). **No error boundary anywhere in the app**, so any crash = blank page. Fix the deref AND add a global ErrorBoundary. | S+S |
| 0.2 | **Port editor 500s everywhere** | Station/port rename left dangling `port` identifiers: `admin.py:1362-1364` (`station = query...; if not port:` NameError), `admin.py:1548/1563`, `admin_comprehensive.py:2093/2495/2549`. GET/PATCH/DELETE on stations all 500. | M |
| 0.3 | **Legacy routers shadow working ones** | `economy.py` (broken: phantom `quantity_available`, `if port` NameErrors) and `combat.py` (broken double self-join `combat.py:68-74`, Integer→UUID dead join) are mounted BEFORE `admin_economy.py`/`admin_combat.py` (api.py:69,72 vs 90,91) and steal `/admin/economy/*` + `/admin/combat/*`. Working analytics-service implementations are unreachable. Fix = delete/re-prefix legacy files. Also: `admin.py` DELETE /galaxy/{id} shadows `bang_galaxy.py`'s proper cascade delete; `admin_ships.py` DELETE shadowed by comprehensive. | M |
| 0.4 | **Async-session plague: 5 whole files dead** | `admin_enhanced.py` (all 5 eps), `admin_factions.py` (7 of 8 — incl. silent no-op commits that never persist), `admin_messages.py` (all 4), `audit.py` (3 of 4), **`mfa.py` (ALL 8 — MFA enrollment/verify cannot work)**. All inject `get_async_session` into sync `.query()`. Mostly one-line `get_db` swaps. | M |
| 0.5 | **UsersManager mutations all 404** | UI invented `/admin/users` CRUD (`UsersManager.tsx:65,106,136,156`); the real working CRUD lives at `/api/v1/users/*` (users.py, admin-gated, 7 endpoints). Point the UI at it. | M |
| 0.6 | **MFA setup triple-broken** | `MFASetup.tsx:30` missing `/v1` prefix; calls undefined `confirmMFASetup`; UI posts `/auth/mfa/confirm` (backend: `/verify`). Plus P0.4's backend breakage. Security-critical path. | S |
| 0.7 | **UI-calls-with-no-backend (consolidated)** | Planet PUT/PATCH/DELETE; station DELETE; warp-tunnel PATCH (PUT exists); sector PATCH (PUT exists); `/admin/combat/logs`; `/admin/combat/disputes/{id}/resolve` (backend: `/{combat_id}/resolve`); `/admin/teams/{id}/action`; player bulk/emergency/extended/assets ops; `/admin/health` (backend `/admin/system/health`); `/regions/{id}/culture`+`/diplomacy` (backend `my-region` variants / missing); `/admin/galaxy/{id}/sectors/add`. Each needs: implement, or rewire UI, or honest-disable. | L |
| 0.8 | **Security Dashboard overview = silent void** | Calls nonexistent `/admin/security/metrics`, renders NOTHING on failure (no error state). Six REAL security endpoints exist unused (`/security/report`, `/alerts`, `/player/{id}/risk`...). Rewire overview to the real report/alerts shape. Audit-logs tab works (after P0.4 audit.py fix). | M |
| 0.9 | **Nexus endpoints unauthenticated** | `nexus.py` GET /status, /stats, /clusters, /clusters/{id} have NO auth — anonymous topology reads. Only true auth gap found. | S |

## P1 — LIES & DATA FIDELITY (real UI showing wrong numbers)

| # | Item | Detail | Size |
|---|------|--------|------|
| 1.1 | **Stations Manager hardcodes** | Every row: security 0, docking fee 100, max_capacity 10000, owner "Independent" (`admin.py:1127-1131`) — stations HAVE owners (Fomalhaut→testpilot) and real slip fees. Also `resource_richness='average'`, `tax_rate=5.0` hardcodes at :1190/:1387 (real tax_rate exists on model!). | M |
| 1.2 | **"100 of 100" caps presented as totals** | Stations says "100 of 100" (3,126 exist); Sectors table caps at 100 of 6,300; Galaxy Map renders 100 sectors; Fleet stats derive from a 50-ship cap (dashboard says 89). Server-side pagination + honest totals needed across list pages. | M |
| 1.3 | **Colonization overview counts every planet as a colony** | "1490 colonies, 8,000,000,100 population" — uncolonized worldgen planets included; New Earth carries 8B pop (data anomaly worth its own look); duplicate Pollux IX cards. Filter = owner != null; sanity-check pop stats. | M |
| 1.4 | **Telemetry zeros that mean "doesn't exist"** | Players page: Session 0.0h / Retention 0.0% (no telemetry — canon prescribes retention from AnalyticsService; wire or em-dash). AI Trading "Recent Activity: 0/min 0 queued" string path. Combat ACTIVE BATTLES card styled alarm-red for value 0. | S–M |
| 1.5 | **Bang page CURRENT GALAXY all em-dashes** | Galaxy exists; bang metadata (version/seed/diameter/islands/clusters) not surfaced from bang_snapshot. | S |
| 1.6 | **/analytics/dashboard partial hardcodes** | `admin_comprehensive.py:1140-1155` server_performance 0.1/0/0, active_traders_24h=0. De-mock or derive. | S |
| 1.7 | **Warp tunnel Usage column = 0** | Usage tracking never increments (verifpilot used one today). Wire counter or drop column. | S |
| 1.8 | **EventManagement mock fallback** | Falls back to hardcoded template data on failure (last surviving mock-fallback in a routed page). | S |
| 1.9 | **Sectors table "Unknown · Unknown" location** | Region/cluster lookup not resolving in list rows. | S |

## P2 — SPEC GAPS (canon requires, nothing exists)

| # | Item | Detail | Size |
|---|------|--------|------|
| 2.1 | **AdminScopeGrant system (ADR-0058)** | 19-scope model + `/admin/scopes`, `/admin/audit`, `/admin/review-queue` pages (Release-tagged). Replaces the design-only RBAC banners. The real fix for Permissions/Roles. | XL |
| 2.2 | **Economy levers panel** | Canon (ui-flows item 18, lifecycle.md §5): regional tax 5–25%, starting credits, turn regen, per-station production/base_price, bounty payouts, upgrade costs, insurance ratios, mint/burn, pause production tick — "exposed in Economy Dashboard". Today: DB edits only. | L |
| 2.3 | **Multi-Account Review page** | OPERATIONS/multi-account-detection.md prescribes a review surface; none exists. | M |
| 2.4 | **EconomicMetrics writer job** | Credits-in-circulation depends on a periodic writer that is Design-only; dashboard falls back to live computation. | M |
| 2.5 | **Admin REST rate limits** | Design-only per canon (5/hour expensive analytics class). | M |
| 2.6 | **Retention rates on dashboard** | Canon: 7/30-day rolling retention from AnalyticsService on the operator hub. Not rendered. | S |

## P3 — REVERSE GAPS (working backend, zero UI)

| # | Item | Detail | Size |
|---|------|--------|------|
| 3.1 | **/admin/fleets — 9 endpoints** | Live battle monitoring, intervention, morale tuning, force-dissolve. Complete and real. No UI. | M |
| 3.2 | **/admin/drones — 8 endpoints** | Stats, force-recall, restore, sector summaries. Properly async, real. No UI. | M |
| 3.3 | **/i18n/admin/* — 5 endpoints** | Translation progress, bulk import, key editing — AMBER-mode tooling, unsurfaced. | M |
| 3.4 | **6 security endpoints** | ai_security_service report/alerts/risk/status/action — Security overview should consume these. | M (with 0.8) |
| 3.5 | **/admin/factions (8) + /admin/messages (4)** | Faction admin + message moderation — broken (P0.4) AND unsurfaced. Fix then surface. | M |
| 3.6 | **Misc uncalled** | first-login /stats; combat /balance (after de-shadow); economy /dashboard-summary; nexus POST /generate (page can't trigger generation!); admin_ships emergency/health-report; game-events block (9 eps, duplicated in admin.py + events.py — consolidate); bang add-region + version. | S each |

## P4 — PRESENTATION & DISPLAY SENSIBILITY (Max's lens)

| # | Item | Judgment & better display | Size |
|---|------|---------------------------|------|
| 4.1 | **Regional Governor = light-theme island** | Whole page white-on-light inside a dark admin. Retheme to admin tokens. Also a 1,242-line monolith worth splitting. | M |
| 4.2 | **Event Management layout** | Five full-width banner rows each holding one number — should be one compact stat-card row (like Dashboard's Galaxy Statistics); CREATE EVENT bar off-theme purple. | S |
| 4.3 | **Players page** | White "Player Metrics" band clashes; metric cards mix real (credits) with dead telemetry (session/retention) at equal visual weight — separate "live" vs "not yet tracked". | S |
| 4.4 | **Galaxy Map** | No region filter/labels/selection; 100-sector cap unlabeled; no connection rendering. Either invest (viewport loading, region lens, click→sector editor) or link out to Sectors. | L |
| 4.5 | **Colonization overview cards** | Mixed-unit "resources" bars (stock vs % vs count) presented as comparable magnitudes — add unit labels (backend shape frozen; label fix is UI-side). Colony cards repeat planet name 2×. | S |
| 4.6 | **Combat red-zero card** | Color semantics: red = needs attention; ACTIVE BATTLES 0 should be neutral. Dual-axis hour chart overkill for current volume — single sparkline + daily bars reads better. | S |
| 4.7 | **90+ native confirm()/alert() across 20 components** | Replace with the in-codebase reference pattern (WipeGalaxyConfirmDialog typed-name confirm) + a toast system. Heaviest: RegionalGovernor (8), StationsManager (8), TeamManagement (7), EventManagement (7). | L |
| 4.8 | **Deep links bounce to /dashboard** | /admin/analytics etc. redirect on load (auth-init race) — breaks bookmarks/sharing. Preserve intended route through auth check. | S |
| 4.9 | **Error-state inconsistency** | Post-de-mock pages have the gold standard (cause-accurate banners + retry). Older pages: silent voids (Security, Nexus status), console-only failures. Standardize one error/empty/loading kit. | M |
| 4.10 | **NPC filler accounts unmarked in Users** | npc_filler_1..7 indistinguishable from humans — add a kind badge/filter. | S |
| 4.11 | **First Login page = the house style to copy** | Filters + table + export + pagination + status chips. Hold it up as the template for list pages (Stations, Sectors, Fleet). Pagination is guessed from page size — wire real totals. | — |

## P5 — DEAD CODE (delete or rescue)

| # | Item | Size |
|---|------|------|
| 5.1 | pages/Universe.tsx (908 lines, 10 tsc errors), AnalyticsReports.tsx (1105), AdminDashboard.tsx, TranslatedDashboard.tsx, ColonizationOverview.tsx + ColonyDetailModal, UniverseEditorPage + UniverseEditor, admin/InterventionPanel, teams/AllianceNetwork + TeamAdminPanel (6 alerts, 3 phantom endpoints) — all zero imports. ~4,500 lines. | M |
| 5.2 | charts/: FleetHealthReport (RESCUE — pairs with unused /admin/ships/health-report), MarketHealthIndicator, PriceChartWidget, TeamStrengthChart (delete or wire). | M |
| 5.3 | UniverseManager.renderGalaxyConfig dead branch; AdminContext dead members. | S |

## CROSS-CUTTING INFRASTRUCTURE

| # | Item | Size |
|---|------|------|
| C.1 | **Global ErrorBoundary** (P0.1's root cause-amplifier) | S |
| C.2 | **Typecheck gate**: `vite build` ships 69 tsc errors in 15 files silently (CombatFeed 23, WebSocketContext 12). Add `tsc --noEmit` to CI/quality gate, burn down. | L |
| C.3 | **Sidebar nav gap**: /economy page (fully working!) unreachable — one-line Sidebar fix. Audit nav vs route table for drift. | S |
| C.4 | **Server-side pagination pattern** for all list pages (P1.2). | M |
| C.5 | **Mount-order hygiene** in api.py — shadowing is a recurring bug class (economy, combat, galaxy-delete, ships-delete). Add a startup route-collision check. | S |

## SUGGESTED BATCHES (NEON-sized)

1. **"Stop the bleeding"** (P0.1, 0.2, 0.3, 0.4-mfa+audit, C.1): crash, port editor, shadowing, MFA/audit async — E4
2. **"Users & Teams admin work"** (P0.5, 0.7-teams, P4.10): wire real CRUD, team actions honest-disable or implement — E3
3. **"Honest lists"** (P1.1, 1.2, 1.9, C.4): station hardcodes → real fields, pagination, location lookups — E3
4. **"Security console"** (P0.8, 0.9, P3.4, rest of P0.4): real security overview + auth gaps — E3
5. **"Fleet & drone command"** (P3.1, 3.2, 5.2-rescue): surface the working fleet/drone admin — E3
6. **"Governor retheme + presentation pass"** (P4.1, 4.2, 4.3, 4.5, 4.6): the display-sensibility batch — E3
7. **"Dead code purge + typecheck gate"** (P5.*, C.2 start) — E2
8. **"Economy levers"** (P2.2) and **"Scopes"** (P2.1): the two big spec builds — each its own run, Max decisions first
