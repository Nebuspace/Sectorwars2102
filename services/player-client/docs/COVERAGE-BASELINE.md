# player-client coverage baseline

**WO:** `WO-TESTCOV-PLAYER-CLIENT-COVERAGE-VISIBILITY-AND-GAPS`
**Captured:** 2026-08-07 (tip includes PR #280)
**Command:** `npm run test:coverage` (`vitest run --coverage` + `@vitest/coverage-v8`)

## Summary

| Metric | Pct |
|--------|-----|
| Statements | 34.84% |
| Branches | 34.81% |
| Functions | 42.84% |
| Lines | 36.31% |

Suite: all green under coverage run. This is a **visibility** baseline, not a CI gate.

## Follow-up WO candidates (money / combat / trade first)

Staged for hub queue — not built in this WO:

1. **WO-TESTCOV-PLAYER-TRADE-DESK** — `src/components/trade/PlayerTradeDesk.tsx` — **SHIPPED** (merged via #283; vitest 5 cases: initiate/accept/offer/error/settled)
2. **WO-TESTCOV-PLAYER-API-CLIENT** — `src/services/api.ts` + `apiClient.ts` — **SHIPPED** (merged via #284; apiClient refresh/JWT + apiRequest error-shaping + trade/combat/grey wrappers)
3. **WO-TESTCOV-PLAYER-MODULE-GRID** — ModuleGrid / Insurance / Maintenance — **SHIPPED** (merged via #285; 9 vitest cases)
4. **WO-TESTCOV-PLAYER-TRADING-INTERFACE-DEPTH** — TradingInterface buy/sell depth — **SHIPPED** (merged via #287; mount + buy-quote + sell-confirm)
5. **WO-TESTCOV-PLAYER-SPACEDOCK-SHELL** — SpaceDock / GamblingVenue — **SHIPPED** (merged via #288; menu/slots controls + slots spin POST)
6. **WO-TESTCOV-PLAYER-AUTH-FORMS** — LoginForm / RegisterForm — **SHIPPED** (merged via #289; MFA prompt seam + register validation; AuthContext mocked)
7. **WO-TESTCOV-PLAYER-ARMORY** — ArmoryVenue / SpaceDock armory purchase — **SHIPPED** (merged via #290; catalog Buy gates + POST `/api/v1/armory/purchase`)
8. **WO-TESTCOV-PLAYER-HAGGLE-DESK-DEPTH** — HaggleDesk open/offer/accept/counter/reject — **SHIPPED** (merged via #291; complements taxInclusiveTotal suite)
9. **WO-TESTCOV-PLAYER-SHIPYARD-PURCHASE** — ShipyardVenue confirm + SpaceDock ships/purchase — **SHIPPED** (PR pending; POST `/api/v1/ships/purchase`)

Auth forms coverage is test-only (hub GO 2026-08-07T21:28:00Z) — does not touch auth/MFA logic.
