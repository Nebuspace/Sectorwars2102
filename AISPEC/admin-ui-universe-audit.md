# Admin UI Universe Audit — Phase 3 Integration Prep

Read-only audit of `services/admin-ui/` to ground the Phase 3 galaxy-generation UI work (form + preview, history table, live log panel, wipe-with-confirm) in existing conventions.

---

## 1. Tech Stack

- **React 18.2** with **TypeScript 5.2** (strict mode, `noUnusedLocals`, `noUnusedParameters`) — `services/admin-ui/package.json:12`, `services/admin-ui/tsconfig.json:18`.
- **Vite 4.4** build tool — `package.json:32`, `vite.config.ts:1`. Dev server on port 3000, proxies `/api` to `http://gameserver:8080` (`vite.config.ts:35-58`).
- **React Router DOM 6.15** — `package.json:16`, `src/App.tsx:2`.
- **Axios 1.15** for REST, **socket.io-client 4.8** present but the admin UI actually uses a **hand-rolled raw `WebSocket`** wrapper at `src/services/websocket.ts:1` (socket.io is unused by this service).
- **i18next 23.16 + react-i18next 13.5** with HTTP backend + browser language detector — `package.json:9-11,15`.
- **D3 7.8** + **Chart.js 4.4 / react-chartjs-2 5.2** for visualisations — `package.json:7,8,13`.
- **No UI component library** (no MUI / Chakra / Tailwind). Plain CSS files colocated with components (one `.css` per `.tsx`), plus a shared design system at `src/styles/design-system.css`. Class naming is BEM-ish but informal.
- JSX runtime `react-jsx` (no `import React from 'react'` strictly required, but every existing file still does it — follow that habit).

---

## 2. Existing Universe Components

Directory `src/components/universe/` — **6 component files** (plus their CSS):

| File | What it does | Phase 3 verdict |
|---|---|---|
| `UniverseEditor.tsx` (419 lines) | D3-rendered interactive sector map: pulls `sectors` from `AdminContext`, falls back to sample/random data if empty, supports region/cluster filtering, click-to-select, tooltip. Heavy with random fallback logic. | **REPLACE-ADJACENT** — Do *not* extend. Phase 3 wants a **preview** of generation output, not an editor. Build a new lightweight `GalaxyPreview` (likely SVG, simpler than D3) sourced from the new bang-generate response. Leave `UniverseEditor.tsx` alone. |
| `SectorDetail.tsx` | Read-only sector detail view (right-side panel after clicking a sector). | KEEP — not in Phase 3 scope. |
| `SectorEditModal.tsx` (~400 lines) | Modal form for editing a single sector with unsaved-changes guard via `window.confirm`. | **PATTERN REFERENCE** for modal layout + dirty-state guard. Do not modify. |
| `PlanetDetail.tsx` + `PlanetDetailModal.tsx` | Planet detail view + modal wrapper. | KEEP. |
| `StationDetail.tsx` | Station detail view (exported as `PortDetail` in some imports — see `UniverseManager.tsx:5`). | KEEP. |
| `ColonyDetailModal.tsx` | Colony detail modal. | KEEP. |

Adjacent universe-related pages in `src/components/pages/`:

- `UniverseManager.tsx` (~700 lines) — **the current galaxy-generation page**, accessible at `/universe`. Holds the existing density/warp-tunnel form, the `window.confirm("A galaxy already exists…")` wipe flow, and a hand-rolled pan/zoom SVG mini-map. **Phase 3 should EXTEND this page** (or carve out a new sub-route under `/universe/bang`) rather than rewriting from scratch. The existing config-state shape (`galaxyConfig` object at `UniverseManager.tsx:43-58`) is a usable starting point.
- `Universe.tsx` — older variant (still imported elsewhere?). Confirm-and-clear at `Universe.tsx:148`. Likely **dead-ish**; not routed in `App.tsx`. Do not touch.
- `UniverseEditorPage.tsx` — thin wrapper around `UniverseEditor`. Not routed in `App.tsx` either.
- `SectorsManager.tsx`, `PlanetsManager.tsx`, `StationsManager.tsx`, `WarpTunnelsManager.tsx` — CRUD tables for each entity. **Pattern reference** for the new generation-history table (column layout, action buttons, `delete-btn` styling — see `WarpTunnelsManager.tsx:89,272`).

---

## 3. State Management

**React Context only — no Redux, no Zustand, no React Query.** Three providers wrap the app in `src/App.tsx:60-63`:

1. `AuthProvider` — `src/contexts/AuthContext.tsx:33` — owns `user`, `token`, login/MFA/refresh.
2. `AdminProvider` — `src/contexts/AdminContext.tsx:208` — **owns all galaxy/region/zone/cluster/sector state** plus user/player lists. Exposes:
   - `galaxyState`, `regions`, `zones`, `clusters`, `sectors` (`AdminContext.tsx:217-221`)
   - `loadGalaxyInfo`, `loadRegions`, `loadRegionZones`, `loadClusters`, `loadSectors`
   - **`generateGalaxy(name, numSectors, config)`** (`AdminContext.tsx:380`)
   - **`generateEnhancedGalaxy(config)`** (`AdminContext.tsx:413`) — already wired to `/admin/galaxy/generate-enhanced`
   - **`clearGalaxyData()`** (`AdminContext.tsx:466`) — already wired to `DELETE /admin/galaxy/clear`
   - `addSectors`, `createWarpTunnel`
   - Global `isLoading` + `error` strings.
3. `WebSocketProvider` — `src/contexts/WebSocketContext.tsx:33` — see §6.

Phase 3 should **add new methods to `AdminContext`** (e.g. `bangGalaxy`, `loadGenerationHistory`, `subscribeToBangLog`) rather than introducing a new context, *unless* the live log buffer would bloat the context — in which case a dedicated `BangContext` or a small hook with `useState` is fine. Components consume via `const { … } = useAdmin()` (`UniverseManager.tsx:15`).

Local component state uses `useState` freely; there is no formal state-management discipline beyond "context for shared, useState for local."

---

## 4. API Client Pattern

**Axios, with relative base URL `/api/v1`.** No central API module — each context creates its own axios instance:

```ts
// AdminContext.tsx:228-230
const api = axios.create({ baseURL: '/api/v1' });
```

**Auth header handling is inconsistent** — three patterns coexist:

1. **Per-call header** (most common in `AdminContext`):
   `await api.get('/admin/stats', { headers: token ? { Authorization: `Bearer ${token}` } : {} });` — `AdminContext.tsx:241-243`.
2. **Global axios default** set after login: `axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`` — `AuthContext.tsx:319`.
3. **Request interceptor** in the shared util `src/utils/auth.ts:16-27` (rarely used — only `api` from this file is imported in a few places).

A response interceptor in `AuthContext.tsx:151-176` catches 401 → calls `refreshToken()` → replays the original request. Phase 3 should rely on this — just use the `AdminContext` pattern (axios instance with `headers: { Authorization: 'Bearer ' + token }` per call).

Errors are caught and stuffed into the context's `error` string + `console.error`'d (`AdminContext.tsx:299-302`). User-facing errors today use `window.alert` (`UniverseManager.tsx:152,159`). Phase 3 can do better, but match the convention if time is short.

Base URL strategy is documented well at `AuthContext.tsx:39-54` — Vite proxy in dev, nginx in prod, never absolute URLs.

---

## 5. i18n Pattern

**i18next + react-i18next**, configured at `src/i18n.ts:48-121`. Key facts:

- Namespaces: `common`, `admin`, `auth` — `i18n.ts:57`. New keys for Phase 3 belong in **`admin`** namespace.
- Translations loaded from the **gameserver API** at `/api/v1/i18n/{{lng}}/{{ns}}` (`i18n.ts:64`) — they are *not* static JSON files in the admin-ui repo. There is a hardcoded fallback dict at `i18n.ts:92-104` for offline degradation.
- Supported locales: `en`, `es`, `zh`, `fr`, `pt`, `de` — `i18n.ts:38-45`.
- Suspense is **disabled** (`useSuspense: false`, `i18n.ts:117`) — t() returns the key as fallback while loading.

**Adoption is shallow.** Only `LanguageSwitcher.tsx` and `TranslatedDashboard.tsx` actually call `useTranslation()`. Most pages — including `UniverseManager.tsx` — are **hardcoded English** ("🌌 Bang a New Galaxy Into Existence!", `UniverseManager.tsx:167`).

**Recommendation for Phase 3**: Use `useTranslation('admin')` from the start so we don't have to retrofit later. Key prefix `admin.bang.*` (form labels, history columns, log filters, wipe-confirm copy). New keys must also be added on the gameserver side under the i18n endpoint — coordinate with backend team or document the key list for them. If that coordination is too heavy for the sprint, hardcode English and leave a `// TODO: i18n` comment matching existing convention.

---

## 6. SSE / WebSocket Precedent

**No SSE (`EventSource`) usage anywhere in admin-ui** — `grep -rn "EventSource"` returns zero hits. Phase 3 introduces the first SSE consumer.

**WebSocket precedent is strong** and worth modelling SSE plumbing after:

- `src/services/websocket.ts:51-345` — `AdminWebSocketService` singleton: connect with `?token=<JWT>` query param, reconnect with exponential backoff up to 5 attempts, 30-second heartbeat, event handler `Map<event, Set<handler>>`, gives-up callback. Compare attack-surface considerations for SSE.
- `src/contexts/WebSocketContext.tsx:33-111` — provider that connects on auth, exposes `subscribe(event, handler)`. Inline custom hooks at lines 114-296 wrap specific event groups (`useEconomyUpdates`, `useCombatUpdates`, `useFleetUpdates`, `useTeamUpdates`, `useSystemAlerts`, `useAIUpdates`).

**Phase 3 SSE design**: build an `EventSource`-based equivalent. Token passed via query param (matching the WS pattern at `websocket.ts:122`) since `EventSource` can't set custom headers. Wrap as `src/services/bangLogStream.ts` + a `useBangLog(galaxyId)` hook that returns `{ lines, isConnected, error }`. Re-use the gives-up / reconnect-backoff pattern.

---

## 7. Auth Flow

1. User hits `/login` → `LoginPage` calls `useAuth().login(username, password)`.
2. `AuthContext.tsx:255` — POST `/api/v1/auth/login/direct` (uses **fetch**, not axios, for "better control").
3. Response either: (a) MFA required → returns `{ requiresMFA: true, sessionToken }`, navigates to MFA form; or (b) tokens returned → `access_token` + `refresh_token` stored in **`localStorage`** under keys `accessToken` / `refreshToken` (`AuthContext.tsx:313-315`).
4. `axios.defaults.headers.common['Authorization'] = Bearer <token>` is set globally (`AuthContext.tsx:319`).
5. Token is auto-refreshed by the axios response interceptor on 401 (`AuthContext.tsx:151-176`).
6. `ProtectedRoute` (`src/components/auth/ProtectedRoute.tsx`) gates routes via `useAuth().isAuthenticated`.

**JWT contains `is_admin`** flag, checked everywhere as `if (!user || !user.is_admin) return;` (e.g. `AdminContext.tsx:234`). Phase 3 must gate the new bang routes with `ProtectedRoute` and likely an additional `is_admin` check in handlers.

For SSE, grab the token via `localStorage.getItem('accessToken')` or `useAuth().token` (the context exposes it at `AuthContext.tsx:503`).

---

## 8. Routing

`src/App.tsx:63-110` — all routes live in one file under a single `AppLayout`. Lazy-loaded via `React.lazy` + `Suspense` (`App.tsx:16-47`). Pattern:

```tsx
const NewPage = lazy(() => import('./components/pages/NewPage'));
// ...
<Route path="universe/bang" element={<ProtectedLazyRoute element={<NewPage />} />} />
```

Existing universe sub-routes (`App.tsx:91-94`):
- `/universe` → `UniverseManager`
- `/universe/sectors` → `SectorsManager`
- `/universe/planets` → `PlanetsManager`
- `/universe/stations` → `StationsManager`
- `/universe/warptunnels` → `WarpTunnelsManager`

**Recommended Phase 3 routes**:
- `/universe/bang` → `BangGalaxyPage` (form + preview)
- `/universe/bang/history` → `BangHistoryPage` (history table + log drawer) — *or* tab inside `BangGalaxyPage`.

Also add a sidebar nav entry in `src/components/layouts/Sidebar.tsx:38-46` under the `universe` group (e.g. `{ to: '/universe/bang', label: 'Bang Galaxy', icon: '💥' }`).

---

## 9. Existing Wipe / Delete Patterns

The codebase universally uses native `window.confirm()` for destructive actions — there is **no shared `ConfirmDialog` component**:

- `UniverseManager.tsx:126-130` — "A galaxy already exists. Would you like to clear…" (the closest precedent for Phase 3 wipe flow).
- `Universe.tsx:148-156` — same pattern in the older page.
- `WarpTunnelsManager.tsx:89-95` — `if (!confirm(\`Are you sure you want to delete tunnel "${tunnel.name}"…\`))`.
- `SectorEditModal.tsx:231,394` — unsaved-changes guard + delete confirmation.

Errors after destructive ops use `window.alert()` (e.g. `UniverseManager.tsx:155,159`). Buttons styled `.delete-btn` with trash emoji (`WarpTunnelsManager.tsx:272`).

**Recommendation**: Phase 3 wipe dialog should be a real **typed-name confirmation modal** ("type GALAXY-NAME to confirm") — a clear upgrade over `window.confirm`. Build it as `src/components/universe/WipeGalaxyConfirmDialog.tsx`. Use plain CSS + an overlay div matching `sector-edit-modal.css` styling for visual consistency.

---

## 10. Recommendations for Phase 3 Sub-Agents

### Naming & Location
- Page components → `src/components/pages/BangGalaxyPage.tsx` (form+preview), `src/components/pages/BangHistoryPage.tsx` (history+log).
- Reusable bits → `src/components/universe/`: `BangForm.tsx`, `BangPreview.tsx`, `BangHistoryTable.tsx`, `BangLogPanel.tsx`, `WipeGalaxyConfirmDialog.tsx`.
- Service / streaming → `src/services/bangLogStream.ts`.
- Context extension → add methods to `AdminContext.tsx` (`bangGalaxy`, `loadBangHistory`, `wipeGalaxy`). Do **not** create a new top-level context.
- CSS files colocated next to TSX, kebab-case (`bang-form.css`).
- **Do not** use the word "enhanced" in new component names without asking Max (CLAUDE.md guidance).

### Conventions To Follow
- **React 18 + TS strict.** Avoid `any`; reuse types from `AdminContext.tsx` (`GalaxyState`, `GalaxyGenerationConfig`, `Region`).
- **Axios via `AdminContext` methods** — do not call `fetch` from components except for SSE (`EventSource`).
- **Plain CSS, no UI library.** Match existing class-naming style (`.galaxy-config-panel`, `.form-group`, `.stat-card`).
- **`useTranslation('admin')`** + `t('bang.form.title')` keys from the start. Avoid `<TranslatedDashboard>`-only adoption.
- **JWT auth**: per-call `Authorization: Bearer ${token}` headers; SSE needs `?token=` query param.

### Five Bullets for the Form Author (BangGalaxyPage / BangForm)
- Extend the existing `galaxyConfig` shape from `UniverseManager.tsx:43-58` (don't redesign from scratch).
- Wire submission through a new `AdminContext.bangGalaxy(payload)` method that POSTs to whatever Phase 3 bang endpoint the gameserver exposes (current parallel is `generateEnhancedGalaxy` at `AdminContext.tsx:413`).
- Use the wipe-confirm flow precedent at `UniverseManager.tsx:122-157`: catch 400 "already exists", offer wipe-via-`WipeGalaxyConfirmDialog`, retry.
- For preview: build a **simple SVG mini-map** in the style of `UniverseManager.tsx:467-588` (don't pull in D3 — that level of interactivity isn't needed for preview).
- Reflect `isLoading` from `useAdmin()` on submit button ("💥 Banging…" disabled state, mirroring `UniverseManager.tsx:330`).

### Five Bullets for the History + Log Author
- Use `WarpTunnelsManager.tsx` as the table layout template (column headers, row actions, `.delete-btn`).
- New SSE stream: build `useBangLog(generationId)` hook in `src/services/bangLogStream.ts` modelled after the WS reconnect/backoff loop in `services/websocket.ts:297-317`. `EventSource(`/api/v1/admin/bang/${id}/log?token=${token}`)`.
- Render log lines in a fixed-height scrolling panel; auto-scroll to bottom unless user scrolled up (use a `scrolledUp` ref).
- History rows clickable → expand to inline `BangLogPanel` *or* navigate to `/universe/bang/history/:id` — pick one and stay consistent.
- Tie history fetch into `AdminContext.loadBangHistory()`. Surface `isLoading` + `error` from context, render a skeleton or "No generations yet" empty state matching `UniverseManager.tsx:469-475` pattern.

---

## Notes for the Caller

- The admin UI is **not empty for universe management** — there is meaningful existing code (`UniverseManager.tsx`, `UniverseEditor.tsx`, sector/planet/station/warp managers) but it is dated, hardcoded-English, and uses `window.confirm`/`window.alert` liberally. Phase 3 should match conventions but quietly upgrade the wipe-confirm UX.
- No tests exist for the universe components beyond Playwright e2e in `services/admin-ui/scripts/`; do not be surprised by the lack of unit tests.
- Socket.io-client is in `package.json` but unused — do not add socket.io as a dependency strategy. The real precedent is raw `WebSocket` + the new `EventSource` Phase 3 will introduce.
