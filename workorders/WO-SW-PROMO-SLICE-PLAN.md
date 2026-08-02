# WO-SW-PROMO-SLICE-PLAN — Schema-first feat→master slice plan

**Status:** READY · EXECUTE · HIGH  
**Seat:** `impl-sectorwars`  
**Branch:** `wo/SW-PROMO-SLICE-PLAN`  
**Repo / zone:** `Sectorwars2102` only  
**Depends:** `feat/expeditions-vista` @ ≥ `cae6f001` · PROMO ≈1090 files ahead of `origin/master`

## Why

Promotion cadence (CLAUDE.md): F ≥ 150 → hub/seat produce a coherent slice plan; F ≥ 200 → land ≤300-file / ≤20k-line (target ~200 / ~15k) PRs to `master`. Mega-PRs hard-block Copilot review. Current divergence is ~1090 files — plan before cut.

## Goal

Write a **parity-coherent, CI-first** ordered slice plan that can be executed as a series of promotion PRs `feat/expeditions-vista` → `master` without straddling model/migration renames.

## Scope

1. Inventory `git diff --name-only origin/master...HEAD` (and line totals via `git diff --numstat`).
2. Propose ordered slices in `.samantha/plans/promo-slice-plan.md` (create/update):
   - **Slice 0 / CI-first:** `.github/workflows/` + their scripts (if any delta).
   - **Schema-first:** when migrations exist in the tranche, ALL migrations + the whole importable backend package that populates `Base.metadata` travel together.
   - Following slices: code-only, coherent by package/feature, each ≤200 files and ≤15k changed lines (hard ceilings 300 / 20k).
3. Flag any slice that touches auth/payments/MFA/admin-gating/AI-safety or migrations as **🧑‍⚖️ Max-GO** before land.
4. This WO file Status → DONE with plan path + tip SHA.

## Out of scope

- Landing the promotion PRs (follow-on WOs `SW-PROMO-SLICE-N` after hub Accepts the plan)
- Force-push / history rewrite
- `tw2002-aiclient` / docs / bang
- Mass-deleting untracked neon-proof debris in the primary tree (separate hygiene if needed)

## Accept

1. `.samantha/plans/promo-slice-plan.md` lists N≥1 slices with file counts + rough line totals + Max-GO flags.
2. Slice 0 is CI-first when workflow deltas exist; else documented why n/a.
3. No slice exceeds target ~200 files / ~15k lines without an explicit "must split further" note.
4. Suite/build for this WO: n/a (plan/docs) — STATUS proves plan file exists and numbers from `git diff`.

## Proof

```bash
git fetch origin master feat/expeditions-vista
git diff --name-only origin/master...HEAD | wc -l
test -f .samantha/plans/promo-slice-plan.md
```

## Constraints

Exclusive worktree of `wo/SW-PROMO-SLICE-PLAN`. Never edit hub primary dirty tree. Explicit paths only — never `git add -A`.
