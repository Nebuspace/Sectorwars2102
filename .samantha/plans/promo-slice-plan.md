# Promotion slice plan — `feat/expeditions-vista` → `master`

**WO:** WO-SW-PROMO-SLICE-PLAN (#160) · **Seat:** `impl-sectorwars` · **Authored:** 2026-08-02
**Measured at:** `origin/master` `f4505f7a` · `HEAD` `e4003ed1` (feat tip `cae6f001` + WO seed)

---

## Verdict

**Do not slice. The mega-diff is a stale-merge-base artifact.**

The branch is not carrying 279,023 lines of unpromoted work. It is carrying **11,320 lines** of genuinely novel work sitting behind a merge-base that is **five weeks old**. Master independently absorbed the rest through 100 other commits while this branch sat.

The correct promotion is **one PR**, not six — but it is gated behind a **conflicted sync** that needs Max's sign-off, because six of the conflicts land on auth / admin-gating / money-path files.

---

## The measurement, and why the two numbers disagree

| measure | files | lines | what it actually compares |
|---|---:|---:|---|
| `origin/master...HEAD` (**three-dot**) | **1091** | **279,023** | merge-base `f801dd5d` (2026-06-28) → HEAD |
| `origin/master..HEAD` (**two-dot**) | **107** | **11,320** | master's *current tip* → HEAD |

Divergence: **584** commits ours / **100** commits theirs. The merge-base is five weeks stale, so the three-dot diff re-reports a month of work master already has.

Corroboration: sampling the three-dot file set, the large majority are **byte-identical between the two tips** — `backend.ts` (7,351 lines), `npc_scheduler_service.py` (5,722), `contract_service.py` (2,116) all resolve to the same blob SHA on both sides. *(Two independent passes put the identical count at 951 and 1003 of 1091; they differ on method — files deleted on both sides register as "differing" in a naive blob compare. Neither figure is authoritative and neither needs to be: the **two-dot diff, 107 files, is the exact answer** and both passes agree on the shape.)*

### The block is real even though the content is phantom

GitHub's PR **Files changed** uses three-dot semantics. So a PR opened **today** displays 1091 files and hard-blocks `copilot_code_review` on both ceilings — exactly what happened to PR #93. The staleness is the cause; the block is still genuine.

Which is why the fix is *sync*, not *slice*. Merging master into feat advances the merge-base to master's tip, and the PR diff collapses from 1091 files to 107.

---

## Plan

### Task A — sync `master` → `feat/expeditions-vista` 🧑‍⚖️ **MAX-GO REQUIRED**

Prerequisite for everything else. **Conflicted: 30 files** (verified in-memory via `git merge-tree --write-tree`, zero mutation).

**🧑‍⚖️ Safety-list conflicts — Max GO before resolution:**

| file | surface |
|---|---|
| `services/gameserver/src/auth/admin_scopes.py` | auth |
| `services/gameserver/src/api/routes/admin_scopes.py` | admin-gating / RBAC |
| `services/gameserver/tests/unit/test_rbac_phase_a1.py` | RBAC |
| `services/gameserver/tests/unit/test_rbac_phase_d1_apis.py` | RBAC |
| `services/gameserver/src/api/routes/genesis.py` | money path |
| `services/gameserver/src/services/genesis_service.py` | money path |
| `services/player-client/src/components/spacedock/GenesisVenue.tsx` | money path (UI) |

**Ordinary conflicts (24)** — resolvable without escalation, but *not* mechanically: `status.py`, `quantum_service.py`, `npc_spawn_service.py`, `intrasystem_movement_service.py`, `PlayerAnalytics.tsx`, `GameDashboard.tsx`, `StatusBar.tsx` + `statusbar.css` + `LocationDropdown.tsx`, `SpaceDockInterface.tsx`, `ConstructionVenue.tsx`, `SolarSystemViewscreen.tsx`, `contactClassification.ts`, `jsdomNodeFetch.ts`, both `package.json`/`package-lock.json` pairs, and 6 associated test files.

Both lockfiles should be **regenerated**, never hand-merged.

**Do not `--autostash`, do not `stash`** — the primary tree currently carries **100 uncommitted entries** belonging to another seat. Sync in an exclusive worktree.

### Task B — single promotion PR `feat` → `master`

After Task A, one PR carries everything:

| | count | ceiling | headroom |
|---|---:|---:|---|
| files | **107** | 300 hard / 200 target | ✅ 64% under target |
| lines | **11,320** | 20,000 hard / 15,000 target | ✅ 25% under target |

Composition (two-dot, 16 added · 19 deleted · 72 modified):

| area | files | lines |
|---|---:|---:|
| `services/player-client` | 33 | 5,466 |
| `services/gameserver` | 34 | 3,313 |
| `.samantha` | 19 | 1,036 |
| `services/admin-ui` | 6 | 507 |
| root + `.claude` + `.neon-proof` + `workorders` + compose/db | 15 | 998 |

**One judgment call for the hub:** `.samantha/` (19 files, 1,036 lines), `.neon-proof/` (1 file), and `CROSS-CLAUDE.gen1-archive.md` (312 lines) are coordination and proof artifacts, not product. They are inside the ceilings so they do not force a split, but promoting session scaffolding to `master` may not be wanted. Dropping them reduces the PR to ~86 files / ~9,700 lines. **Hub's call — flagged, not decided.**

---

## Required checks — all resolved

**CI-first (Slice 0) — n/a, documented.** All 4 changed workflows (`ci-build-test`, `ci-core-loop-playthrough`, `ci-schema-parity`, `ci-lint`; 511 lines) are **already byte-identical on master's tip**, which additionally carries `ci-bang-version-check` and `docker-publish-services`. There is no CI delta to promote first. Master is already CI-gated.

**Schema-first — n/a, and this is the strongest result.** Alembic: master **111** version files, HEAD **110**. Exactly **one** file exists on master and not HEAD (`9c46d8ea0c11_seed_system_health_view_scope.py`, whose `down_revision` is `a7c4e91b2d08` — HEAD's own single head). **Zero** exist on HEAD and not master.

HEAD's migrations are a strict **subset** of master's. Master's chain = HEAD's chain + one clean commit. There is **no migration to promote**, no straddle risk, no parity crisis. The "42 changed migration files / 3,478 lines" in the three-dot view is entirely already-synced content.

HEAD's own chain independently verified: 110 files, **single head** `a7c4e91b2d08`, zero broken `down_revision` refs, merge-tuples flattened.

**Renames — zero.** `git diff -M --diff-filter=R` at default *and* 40% similarity returns nothing in either direction. The known parity-breaker (a rename straddling a slice boundary) cannot occur here.

**Per-file feasibility — pass.** No single file exceeds 20,000 changed lines. Largest is `backend.ts` at 7,351 — and it is byte-identical to master, so it is not even novel.

---

## What would invalidate this plan

1. **Master moves again.** Every number is measured against `f4505f7a`. Master is actively receiving promotion PRs. Re-measure both diffs immediately before executing Task A; if master advanced, the conflict set changes.
2. **Conflict resolution changes behaviour.** 30 files resolved by hand is where regressions enter. Post-sync, the full gate must pass before Task B opens — resolving a conflict is *editing code*, not bookkeeping.
3. **Someone opens the PR before Task A.** It renders at 1091 files, blocks Copilot permanently, and has to be closed and reopened.

## Execution order

1. 🧑‍⚖️ **Max GO** on the 7 safety-list conflicts → **blocking**
2. Re-measure both diffs against current master
3. Task A sync in an exclusive worktree · regenerate both lockfiles · resolve 30 conflicts
4. Full gate green (`npm run build` · `tsc --noEmit` · `pytest` · `ruff`) — build-green is necessary, not sufficient; conflicts touched runtime paths
5. Verify PR diff renders **≤300 files / ≤20k lines** before requesting review
6. Task B: single promotion PR → master
7. Hub Accept · update the promotion gauge

---

## Cadence note

`CLAUDE.md` cites this branch at 1074 files as the origin incident for the promotion cadence, and the gauge reads ~1090 today — which looks like the cadence failed. It did not fail; **the gauge is measuring the wrong thing.** `pr-size-guard.sh` reports three-dot, so a branch whose content is already merged still reads as catastrophically diverged. Recommend the gauge report **both** numbers, with two-dot as the "real work outstanding" figure — otherwise every stale branch triggers a slice-plan for a phantom.
