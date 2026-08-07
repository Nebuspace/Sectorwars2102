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
4. **WO-TESTCOV-PLAYER-TRADING-INTERFACE-DEPTH** — TradingInterface buy/sell depth — **SHIPPED** (PR pending; mount + buy-quote + sell-confirm)
5. **WO-TESTCOV-PLAYER-SPACEDOCK-SHELL** — SpaceDock / GamblingVenue — **SHIPPED** (PR pending; menu/slots controls + slots spin POST)

Zero-coverage auth forms (`LoginForm` / `RegisterForm` / …) exist but sit next to the MFA safety list — leave for an explicit auth-test WO after the MFA fix lands, not this batch.
