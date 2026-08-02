# WO-ADM-ROUTE-SMOKE-E2E — Admin-ui Playwright route smoke

**Status:** OPEN · MED · seat `impl-sectorwars-cursor` (Cursor Agent)  
**Posted:** 2026-08-02T03:10Z · hub multi-seat fill  
**Branch:** `wo/ADM-ROUTE-SMOKE-E2E`  
**Repo / zone:** `Sectorwars2102` · **paths:** `services/admin-ui/**` + this WO only  
**Depends:** none · base `feat/expeditions-vista` @ ≥ `3c3d19f0`

## Goal

Add Playwright route-smoke coverage for admin-ui pages that lack it, so broken route wiring fails CI instead of silent production.

## Scope

1. Exclusive worktree of `wo/ADM-ROUTE-SMOKE-E2E` (not the dirty primary tree).
2. Extend existing Playwright setup under `services/admin-ui/`.
3. Cover ≥5 distinct routed pages (navigate → visible landmark / no crash). Auth-blocked routes may `test.skip` with reason.
4. Cheap Scroll-Law / geometry asserts welcome where fixtures already exist; not required for every route.

## Out of scope

- `services/gameserver/**` (hub-mediated overlap — do not touch)
- `services/player-client/**`
- New backend endpoints
- Primary-tree hygiene (sibling CC WO)
- Changing product routes except test-only helpers

## Accept

1. ≥5 new or extended smoke tests green via the package's documented Playwright/npm script.
2. `services/admin-ui` build + existing CI admin-ui job still green on the PR.
3. STATUS lists routes covered + any honest skips.

## Proof

```bash
# from services/admin-ui (exact script name per package.json / README)
npm run build
# playwright smoke as already wired in CI or package scripts
```

`live-prove`: **n/a** (offline e2e / CI).

## Constraints

ZONE-ROUTING: admin-ui only. Explicit paths — never `git add -A`. No DEPLOY-WINDOW (frontend-only in exclusive lane → optional HEADS-UP).
