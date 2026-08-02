# WO-SW-PROMO-TASK-A-SYNC — master → feat sync (careful file-by-file)

**Status:** PREVIEW · awaiting seat ratification · HIGH  
**Posted:** 2026-08-02T03:42Z · hub after Max ruling  
**Branch:** `wo/SW-PROMO-TASK-A-SYNC`  
**Seat (proposed executor):** `impl-sectorwars` (Claude Code)  
**Sibling:** `impl-sectorwars-cursor` — stay off this WT; no GS edits during sync  
**Refs:** `.samantha/plans/promo-slice-plan.md` · Max 2026-08-02 "merge carefully file by file… hub decides, ask implementers to ratify"

## Goal

Advance merge-base by merging `origin/master` into `feat/expeditions-vista` so a later Task B promotion PR renders ~107 files (not ~1090). Resolve all conflicts carefully; regenerate lockfiles.

## Max ruling (2026-08-02)

- Strategy: **merge carefully, file-by-file** (not blanket "ours" / "theirs").
- Hub proposes resolution principles below.
- **Execute only after** implementer ratification (`🤝 ACK [TASK-A-RATIFIED]` from executor; sibling ACK lane-clear).

## Hub resolution principles (proposed — ratify or push back)

### Safety-list (7) — file-by-file, both sides read before choosing

| File | Principle |
|------|-----------|
| `auth/admin_scopes.py` + `routes/admin_scopes.py` | Preserve **strictest** effective permission model: union of deny/missing-scope fails-closed behavior; keep master's security fixes; keep feat's intentional new scopes only if they remain fail-closed and tested. Never drop a scope check present on either side without STATUS callout. |
| `test_rbac_phase_a1.py` + `test_rbac_phase_d1_apis.py` | Tests must match the **merged** auth behavior. Prefer keeping both sides' assertions when compatible; if conflict, rewrite test to pin the merged fail-closed semantics — do not weaken coverage to green. |
| `routes/genesis.py` + `services/genesis_service.py` | Money-path: keep master's economic/safety invariants; integrate feat features only when they do not bypass balances, ownership, or deploy gates. Prefer master's validation order if feat loosens it. |
| `GenesisVenue.tsx` | UI must not expose actions the merged backend rejects; prefer feat UX only where API contract still holds. No silent "always enabled" deploy controls. |

**Per file Accept note required** in STATUS: 2–4 lines — what each side wanted, what you kept, residual risk.

### Ordinary conflicts (~24)

Same care, no Max stop: resolve file-by-file; regenerate `package-lock.json` files via package manager (never hand-merge locks). `package.json` merge then `npm install` in the affected package dirs.

### Process

1. Re-measure `merge-tree` vs current `origin/master` before starting (conflict set may have moved).
2. Exclusive worktree only — never primary tree; never `--autostash` / `stash`.
3. Merge commit (or merge + follow-up fix commits) on this WO branch → PR into `feat/expeditions-vista` (not master).
4. Full gate green before STATUS DONE.
5. **Do not open Task B** until hub Accepts Task A.

## Out of scope

- Promotion PR to `master` (Task B)
- Force-push / history rewrite
- Editing sibling Cursor admin-ui WOs
- Blind `git checkout --ours/--theirs` on safety-list files

## Accept

1. Both Sectorwars seats ratified principles (or hub folded their pushback) **before** conflict resolution commits.
2. `origin/master` merged into branch tip; three-dot file count toward master collapses toward two-dot novel set (STATUS shows before/after `git diff --name-only origin/master...HEAD | wc -l`).
3. All 7 safety files have per-file disposition notes in STATUS.
4. Lockfiles regenerated; gameserver + player-client + admin-ui CI green on the sync PR.
5. No primary-tree dirt from this WO.

## Proof

CI on PR + STATUS disposition table. `live-prove`: n/a for sync itself; Task B may need live later.

## Serialization

If `WO-P9-NPC-CRASH-WATERMARK` (#163) is in flight: **park it** (`HOLD` / STATUS parked) until Task A Accepts — same seat cannot own two exclusive feat-mutating lanes; Task A is the priority Max just unblocked.
