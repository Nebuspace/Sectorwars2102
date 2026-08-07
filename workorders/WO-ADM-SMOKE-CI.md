# WO-ADM-SMOKE-CI — Run admin-ui route-smoke in GitHub Actions

**Status:** OPEN · MED · seat `impl-sectorwars-cursor` (Cursor Agent)  
**Posted:** 2026-08-02T03:35Z · hub autonomous refill after #162  
**Branch:** `wo/ADM-SMOKE-CI`  
**Repo / zone:** `Sectorwars2102` · paths: `services/admin-ui/**`, `.github/workflows/**` (admin-ui job only) + this WO  
**Depends:** #162 MERGED (route-smoke suite on feat)

## Goal

Make `npm run test:e2e:smoke` a **CI gate** on admin-ui PRs so route-smoke cannot rot silently (today CI admin-ui job is build-only).

## Scope

1. Exclusive worktree of `wo/ADM-SMOKE-CI`.
2. Extend the existing admin-ui GitHub Actions job (or add a focused job) to install Playwright browsers as needed and run `test:e2e:smoke`.
3. Keep offline stub harness from #162 (no gameserver / no DEPLOY-WINDOW).
4. Do not touch `services/gameserver/**` or `services/player-client/**`.

## Out of scope

- Expanding route coverage beyond what #162 landed (separate WO)
- Changing product routes
- Primary-tree edits

## Accept

1. On this PR, CI runs route-smoke and it is green (or required check equivalent).
2. `npm run build` still green.
3. STATUS shows the workflow path + job name + sample run URL.

## Proof

CI run on the PR head. `live-prove`: **n/a** (CI/offline e2e).

## Constraints

ZONE-ROUTING: admin-ui + workflow wiring only. Explicit paths — never `git add -A`.
