# WO-SW-PRIMARY-TREE-HYGIENE — Restore primary-tree clean invariant

**Status:** OPEN · HIGH · seat `impl-sectorwars` (Claude Code)  
**Posted:** 2026-08-02T03:10Z · hub after #160 Accept  
**Branch:** `wo/SW-PRIMARY-TREE-HYGIENE`  
**Repo / zone:** `Sectorwars2102` only · **execution tree:** primary `/Users/mrathbone/github/Nebuspace/Sectorwars2102` on `feat/expeditions-vista`  
**Depends:** #160 MERGED (`3c3d19f0`)

## Goal

Restore a checkable clean primary working tree so sibling Cursor seats can treat "primary dirty" as a defect again.

## Scope

At primary tree only:

1. **Tracked mods (5):** `.gitignore`, `.samantha/agents/mack/MEMORY.md`, `.samantha/agents/monk/MEMORY.md`, `.samantha/plans/solo-burst-consolidation.md`, `.samantha/references/README.md` — commit keepers with explicit paths **or** revert accidental churn (prefer keep useful `.gitignore` rules; revert agent MEMORY noise unless clearly intentional).
2. **Untracked debris (~92):** delete or gitignore abandoned `.neon-proof/**` and other proof debris. Prefer gitignore + delete local files so they do not reappear in `git status`.
3. If real product WIP appears that is not debris — **STOP** and STATUS for hub; do not invent disposition.

## Out of scope

- `master`→`feat` sync / promo Task A (Max-gated conflicts) / Task B promotion PR
- Force-push / history rewrite
- `tw2002-aiclient` / docs / bang
- Editing `services/admin-ui` for the Cursor sibling's WO

## Lane

Seat owns the **primary tree** exclusively for this WO. Sibling `impl-sectorwars-cursor` stays in worktrees only. Hub seed WT `/private/tmp/sw-hub-hygiene` is idle after HANDOFF — do not share checkouts.

## Accept

1. `git status --short` empty, **or** a declared keep-list with hub ACK in STATUS.
2. STATUS includes before/after `git status --short | wc -l` and disposition table (commit / revert / delete / gitignore) per path class.
3. Explicit-path commits only — never `git add -A`.

## Proof

```bash
cd /Users/mrathbone/github/Nebuspace/Sectorwars2102
git status --short | wc -l   # → 0 (or hub-ACK keep-list)
```

`live-prove`: **n/a** (hygiene / no product runtime).

## Constraints

No stash/autostash on multi-seat trees. No touching Cursor's worktrees. Push hygiene commits on `wo/SW-PRIMARY-TREE-HYGIENE` (or commit on feat only if hub re-rules — default: this WO branch, PR → `feat/expeditions-vista`).
