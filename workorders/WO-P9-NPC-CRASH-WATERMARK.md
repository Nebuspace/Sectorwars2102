# WO-P9-NPC-CRASH-WATERMARK — Scheduler Loop A/B/C crash-recovery watermark

**Status:** OPEN · HIGH · seat `impl-sectorwars` (Claude Code)  
**Posted:** 2026-08-02T03:35Z · hub autonomous refill  
**Branch:** `wo/P9-NPC-CRASH-WATERMARK`  
**Repo / zone:** `Sectorwars2102` · paths: `services/gameserver/**` (+ this WO) · **not** `services/admin-ui/**`  
**Refs:** `audit/BACKLOG.md` · `audit/backlog-regen-2026-07-16/QUEUE-FULL.md` § P9-realtime-npc-crash-watermark · canon npc-scheduler crash recovery

## Goal

Record a last-completed-cycle watermark per loop (A/B/C) in durable state. On restart, execute **bounded catch-up** (per-NPC; realtime events suppressed as stale) instead of process-relative cadence that silently skips downtime.

## Scope

1. Exclusive worktree of `wo/P9-NPC-CRASH-WATERMARK` from current `feat/expeditions-vista`.
2. Reuse durable sweep-anchor patterns (`_read_sweep_anchor` / `_sweep_due_and_advance` on `Galaxy.state`) for Loop A/B/C cadence — verify-first vs HEAD; do not re-implement what already landed for sub-daily sweeps.
3. Catch-up advances NPCs to canonical schedule position; `_broadcast_events` receives **zero** frames for catch-up moves.
4. Watermark stamps forward in the **same transaction** as the work (crash-between rolls both back).

## Out of scope

- `services/admin-ui/**` (Cursor sibling)
- Promo Task A / master sync (Max-gated)
- Redis fanout / lodging / other P9 items
- Force-push

## Accept

1. pytest: stamp Loop A watermark 6h in the past → one wake advances NPCs per canonical schedule; zero catch-up broadcast frames; watermark advances atomically with work.
2. gameserver compile + DB-free pytest green on PR.
3. STATUS cites file:line for watermark read/write + catch-up suppression.

## Proof

```bash
# gameserver unit/integration tests covering watermark catch-up
# CI: gameserver job green
```

`live-prove`: optional stage restart after Accept if DEPLOY-WINDOW available; otherwise offline pytest Accept-sufficient for this WO (STATUS may note live follow-up).

## Constraints

Explicit paths — never `git add -A`. No DEPLOY-WINDOW unless you change shared runtime on stage (then REQUEST via hub). Primary tree stays clean — use exclusive WT only.
