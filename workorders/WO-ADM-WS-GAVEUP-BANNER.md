# WO-ADM-WS-GAVEUP-BANNER — Visible admin-ui banner when WS reconnect is abandoned

**Status:** OPEN · MED · seat `impl-sectorwars-cursor`  
**Posted:** 2026-08-02T03:52Z · hub catch-up refill  
**Branch:** `wo/ADM-WS-GAVEUP-BANNER`  
**Paths:** `services/admin-ui/**` + this WO only — **no gameserver** (Task A owns GS)

## Goal

When admin WebSocket reconnection is abandoned (`hasGivenUp` / `onGaveUp`), show a clear, dismissible/reconnectable UI banner in the admin shell — not only `console.log`.

## Scope

1. Exclusive worktree. Do not touch Task A WT or `services/gameserver/**`.
2. `WebSocketContext` already tracks `hasGivenUp`; wire `AppLayout` (or equivalent shell) to surface it.
3. Offer a manual "Retry connection" that clears gave-up and calls reconnect if the service supports it.
4. Add/extend a small unit or Playwright assertion if cheap.

## Accept

1. Abandoned reconnect is visible in the UI without opening DevTools.
2. Retry control works (or STATUS documents why blocked).
3. admin-ui build + route-smoke CI still green.

## Proof

`npm run build` + `npm run test:e2e:smoke` (or CI). `live-prove`: n/a.
