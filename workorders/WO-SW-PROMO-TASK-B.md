# WO-SW-PROMO-TASK-B — Promote feat→master (product only)

**Status:** OPEN · HIGH · seat `impl-sectorwars`  
**Posted:** 2026-08-02T10:01Z · hub after #165 Accept + Max A1 ruling  
**Branch:** `wo/SW-PROMO-TASK-B`  
**Base:** `feat/expeditions-vista` @ ≥ `b388b180` (#165) · promote **into `master`**  
**Refs:** `.samantha/plans/promo-slice-plan.md` · Max ruling 2026-08-02 product-only

## Goal

Open **one** promotion PR `feat/expeditions-vista` → `master` carrying **product only** (~40 files / ~2,174 lines per Max remeasure after Task A). Advance the promotion cadence.

## Max ruling (standing)

**PRODUCT ONLY.** Exclude coordination / process artifacts from `master`:

- `.samantha/references/**`, `.samantha/plans/**`, `.samantha/agents/*/MEMORY.md`
- `workorders/**`
- `.neon-proof/**`
- `CROSS-CLAUDE.gen1-archive.md`

(Exact list as measured in seat STATUS — re-measure before open; drop any path that is not product.)

## Scope

1. Exclusive worktree from current feat tip (after #165).
2. Re-measure two-dot / three-dot vs `origin/master`; confirm ≤300 files / ≤20k lines **with exclusions applied**.
3. Open PR → `master` with only product paths (filter exclusions via PR path list / commits that don't add excluded trees — prefer a clean commit that does not contain excluded files).
4. Do **not** force-push feat; do not rewrite history.

## Out of scope

- Re-doing Task A sync
- `#163` P9 watermark (stays PARKED until Task B Accepts)
- `admin-ui`-only Cursor lane

## Accept

1. PR to `master` green on required checks; Copilot can review (under ceilings).
2. STATUS lists included file count / line count **and** excluded paths.
3. No coordination artifacts in the PR file list.
4. Hub merges after Accept (you do not merge `master` unilaterally unless hub says so).

## Proof

CI on the promotion PR. `live-prove`: per surfaces touched — STATUS honest; money-path/safety slices already on feat from prior work should not newly require sacrificial arm for a pure promote if no new runtime delta vs feat tip (document).

## Constraints

Explicit paths. No `git add -A`. Soft-ceiling worktree discipline (live only).
