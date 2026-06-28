# Phase 9 — Frontend modernization

**Status**: DRAFT — awaiting Max's go-ahead
**Owner**: Claude (implementation), Samantha (review)
**Scope branch target**: `master`
**Predecessor**: Phase 7 (frontend code-scanning via PR #40) complete
**Independent of**: Phase 8 (backend mod). Can run in parallel.

---

## Goal

Land the two open Dependabot frontend major PRs (#35 admin-ui, #36 player-client). Both are React 18 → 19 cascades stacked with multiple other framework majors — neither is a "bump and merge" PR. Treat them as deliberate framework upgrades that need real validation in a running browser, not just a CI green.

## What's in scope

### PR #35 — admin-ui majors (15 deps)

| Dep | From → To | Concern |
|---|---|---|
| `react` + `react-dom` | 18.3.1 → 19.2.6 | **React 19**: new compiler hooks, ref-as-prop, deprecated `forwardRef` pattern, new error semantics in `useEffect`. Strict-mode behavior tightened. |
| `vite` | 4.5 → 8.0 | **Major version cascade 4 → 5 → 6 → 7 → 8.** Each had config-shape changes. Vite 5 dropped Node 14; Vite 6 changed environment API; Vite 7+ require ESM-only plugins. |
| `react-router-dom` | 6 → 7 | **API evolution**: `createBrowserRouter`/data routers are the recommended direction in v7, but `<Routes>` + `<Route>` declarative API is still supported. Review admin-ui routing for compatibility and migrate where it provides clear benefit. |
| `react-i18next` | 13 → 17 | Hook signature changes; `useTranslation` namespace param semantics shifted. |
| `i18next` | 23 → 26 | Resource type augmentation reworked (`ResourceNamespaceMap`); type errors expected. |
| `i18next-http-backend` | 2 → 4 | Default fetch behavior tightened; options renamed. |
| `typescript` | 5.9 → 6.0 | TS 6 introduces stricter strict-mode defaults; `Object.entries()` return type changes. |
| `eslint` | 8 → 10 | Major version cascade; flat config required in 9+. |
| `eslint-plugin-react-hooks` | 4 → 7 | Stricter exhaustive-deps; will surface new warnings. |
| `@vitejs/plugin-react` | 4 → 6 | Vite + plugin must match version pair. |
| `@types/*` | various | Type follow-ups for React 19 / Node 25. |

### PR #36 — player-client majors (17 deps)

Includes everything in #35 PLUS:

| Dep | From → To | Concern |
|---|---|---|
| `three` | 0.158 → 0.184 | We already smoke-tested 0.158 → 0.184 in PR #34. This PR brings it from `master` baseline (which already has the bump). May be no-op or near-no-op. |
| `@react-three/fiber` | 8 → 9 | **React 19 + R3F 9 required pairing.** Hook API changes; `<Canvas>` defaults different. |
| `@react-three/drei` | 9 → 10 | Helpers reworked; some components removed/renamed. |
| `framer-motion` | 10 → 12 | Renamed to `motion`; API surface preserved but import paths changed. |
| `react-spring` | 9 → 10 | Spring config defaults changed; will affect animation timing. |
| `@typescript-eslint/*` | 8.32 → 8.60 | Minor bumps; should be safe. |

---

## Why this isn't a bulk merge

React 18 → 19 alone is a deliberate framework upgrade. Combined with Vite 4 → 8, react-router-dom 6 → 7, and React Three Fiber 8 → 9 (for player-client), you're effectively rewriting both frontends. Past projects that bulk-merged this kind of cascade have spent 2-4 weeks chasing runtime regressions.

The two PRs need a verification matrix per UI surface, not a `merge → done`.

---

## Verification strategy

### Stage 1 — Lockfile install + typecheck (Mac)
- `cd services/admin-ui && npm install --legacy-peer-deps && npm run build` — build succeeds
- `cd services/player-client && npm install --legacy-peer-deps && npm run build` — build succeeds
- Both: `npx tsc --noEmit` clean (or catalog new errors)

### Stage 2 — Build on interstitch
- Pull master, `docker compose --profile development up -d --build admin-ui player-client`
- Containers come up healthy

### Stage 3 — Browser smoke per UI surface

**admin-ui** (`https://sw2102-stage.shouden.us/admin`):
- [ ] Login flow renders + completes
- [ ] Sidebar nav between Dashboard / Universe / Bang Galaxy / Players works (validates react-router-dom 7 migration)
- [ ] Bang Galaxy page form renders; preview button works (validates AdminContext + SSE)
- [ ] Wipe-galaxy typed-name confirm modal works
- [ ] i18n strings render (validates react-i18next 17 + i18next 26)

**player-client** (`https://sw2102-stage.shouden.us/`):
- [ ] Login screen renders
- [ ] After login, dashboard renders
- [ ] Galaxy 3D view (`/galaxy/map`) renders + interacts (validates React Three Fiber 9 + @react-three/drei 10 + three.js)
- [ ] First-login flow walks through (validates AI dialogue UI, framer-motion 12 animations)
- [ ] WebSocket realtime events deliver (Spring 10 animation transitions on incoming messages)

### Stage 4 — Playwright e2e
- Run the existing `e2e_tests/bang/*.spec.ts` suite against the stage tunnel
- Any failing tests get triaged: real regression vs selector brittleness (some selectors use role/text not data-testid per Phase 4B audit)

---

## Suggested PR landing order

The frontends are independent → can do either first. My pick: **admin-ui first**, since the bang flow there is the most-recently-tested surface (Phase 3).

| Order | PR | Verification | Notes |
|---|---|---|---|
| 1 | **#35 admin-ui majors** | Stages 1–3 with focus on the Bang Galaxy page | If react-router-dom 7 migration drags, split into smaller PRs |
| 2 | **#36 player-client majors** | Stages 1–4 with focus on Galaxy 3D | React Three Fiber 9 is the highest-risk leg |
| 3 | Add `data-testid` props to Phase 3 bang components | None | Removes Playwright selector brittleness flagged in Phase 4B audit |

## Risk + rollback

- **Risk**: React 19's stricter `useEffect` cleanup behavior + ref-as-prop pattern have surfaced regressions in mature codebases. Three.js + R3F 9 pairing on player-client is also a known sharp edge.
- **Rollback per PR**: same as Phase 8 — squashed commit on master, `git revert <sha>`, push, interstitch picks up on next rebuild.
- **Fallback**: if the React 19 cascade proves too risky, split into two phases — Phase 9α land Vite + TS bumps (no React change), Phase 9β attempt React 19 with a longer validation window.

## Open questions for Max before starting

1. **Vite 4 → 5 vs 4 → 8**: the safer intermediate would be Vite 4 → 5 only. But Dependabot is bundling everything; we'd have to manually split. Worth the effort to get an intermediate stop, or accept the 4 → 8 cascade?
2. **React 19 timing**: React 19 has been GA since late 2024 but the React Three Fiber 9 release is more recent. If R3F 9 has rough edges that haven't surfaced yet, player-client may need to stay on React 18 longer.
3. **Time budget**: estimate is **4-8 hours per UI** to land + verify cleanly. Admin-ui likely faster (less surface area, no 3D). Want one session per UI or a longer combined session?

## What this phase does NOT include

- New UI features
- Visual redesign (component library swaps, theme changes)
- Performance optimization passes
- Admin-ui accessibility audit (separate phase if wanted)

---

*Phase 9 lands the frontend modernization queue. Phase 8 ([backend modernization](./phase-8-backend-modernization.md)) is independent and can run in parallel.*
