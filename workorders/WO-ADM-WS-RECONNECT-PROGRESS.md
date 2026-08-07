# WO-ADM-WS-RECONNECT-PROGRESS — Show reconnect attempt progress in admin shell

**Status:** OPEN · MED · seat `impl-sectorwars-cursor`  
**Posted:** 2026-08-02T10:32Z · hub filler (seat asked; post-#166)  
**Branch:** `wo/ADM-WS-RECONNECT-PROGRESS`  
**Paths:** `services/admin-ui/**` + this WO only — **no gameserver**

## Goal

While admin WebSocket is reconnecting (not connected, not yet `hasGivenUp`), the existing shell chip should show **attempt progress** (e.g. `Reconnecting… (2/5)`), not only the bare label. Operators can see the client is still trying before the #166 gave-up banner appears.

## Scope

1. Exclusive worktree. Do not touch `services/gameserver/**` or Task B WT.
2. Expose reconnect attempt count + max from `websocket.ts` through `WebSocketContext` (read-only).
3. Update the existing reconnecting chip in `AppLayout` to display progress; keep gave-up banner behavior from #166 unchanged.
4. Cheap Playwright or unit pin if easy (optional but preferred).

## Out of scope

- Changing max attempts / backoff policy
- Gameserver / DEPLOY-WINDOW
- Mass worktree prune (separate `🧹 PRUNE-INTENT`)

## Accept

1. Chip shows attempt N of max while reconnecting.
2. Gave-up path still uses #166 banner (no regression).
3. `npm run build` + `npm run test:e2e:smoke` green.

## Proof

Build + smoke. `live-prove`: n/a (admin-ui offline product).
