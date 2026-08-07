# WO-BOUNTY-REALTIME-EVENTS

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/BOUNTY-REALTIME-EVENTS`  
**Base:** `feat/expeditions-vista` @ `3cc84cbc`

## Goal

Close the residual bounty realtime gap: player-client must consume the
already-emitted `bounty_updated` WS frames so StatusBar `bounty_total` /
credits update without polling.

## Verify-first (against HEAD)

| Layer | Status |
|---|---|
| `websocket_service.send_bounty_update` | ✅ live |
| `ranking.py` place/cancel post-commit emit | ✅ live (`action=placed\|cancelled`) |
| `combat_service._emit_bounty_collected` | ✅ live (`action=collected`) |
| player-client `WebSocketContext` handler | ❌ missing (Unhandled warning) |
| player-client live `bounty_total` refresh | ❌ missing |
| admin-ui bounty surface | absent (out of scope — separate tooling) |
| Canon `bounties.md` / `realtime-bus.md` 📐 markers | stale vs code (Max-gated docs, not this WO) |

**Sibling skip:** `WO-NPC-TRADER-TARIFF-WIRING` already delivered (tariff + tenure).

## Scope

- `services/player-client/src/services/websocket.ts` — `BountyUpdatedMessage` + `onBountyUpdated`
- `services/player-client/src/contexts/WebSocketContext.tsx` — `bounty_updated` case + signal/payload
- `services/player-client/src/contexts/GameContext.tsx` — refresh playerState when current player is placer/target/collector
- unit test pinning the WebSocketContext consumer

## Out of bounds

- No gameserver runtime changes (emit already shipped)
- No admin-ui bounty board (not present; soft-cap/admin tooling is separate)
- Stay clear of DETECT-REP / faction-rep / contraband (CC)
- No `sw2102-docs` push (stale 📐 markers noted only)

## Accept

1. `bounty_updated` is handled in WebSocketContext (no Unhandled warning path)
2. Signal + `lastBountyUpdated` stash for placed / collected / cancelled
3. When the current player is placer, target, or collector, `refreshPlayerState` runs so StatusBar bounty/credits move live
4. Vitest pins the WebSocketContext consumer

## Proof

- `npx vitest run src/contexts/__tests__/WebSocketContext.bountyUpdated.test.tsx`
- `npm run build` (player-client) green
- live-prove: n/a for this residual (unit pin of consumer; server emit already live on feat)
