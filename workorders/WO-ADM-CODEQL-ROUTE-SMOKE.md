# WO-ADM-CODEQL-ROUTE-SMOKE — Fix CodeQL js/incomplete-sanitization in route-smoke

**Status:** OPEN · HIGH · seat `impl-sectorwars-cursor`  
**Posted:** 2026-08-02T10:43Z · hub · unblocks Task B promo #169  
**Branch:** `wo/ADM-CODEQL-ROUTE-SMOKE`  
**Paths:** `services/admin-ui/playwright/e2e/route-smoke.spec.ts` + this WO — **no gameserver**

## Goal

Clear the **blocking** CodeQL alert `js/incomplete-sanitization` at `route-smoke.spec.ts:34` on feat (blocks master `code_scanning` high_or_higher for promo #169).

## Context

`new RegExp(\`${path.replace(/\\//g, '\\\\/')}\\/?$\`)` only escapes `/`. Paths are hard-coded literals in the same file (zero untrusted input; e2e test never ships) — real risk none, but the gate still fails.

**Preferred fix:** stop using RegExp — use Playwright's literal `toHaveURL` / string or `URL` equality against the hard-coded path (or escape all regex metacharacters if RegExp must stay).

## Out of scope

- Patching promo branch `promo/task-b-product` / PR #169 directly (CC refreshes after this lands on feat)
- `status.py` / CodeQL medium `py/stack-trace-exposure` (Max-gated; separate)
- Gameserver

## Accept

1. CodeQL alert on that path gone (or rule no longer flags the line) on the PR / tip.
2. `npm run test:e2e:smoke` still green.
3. STATUS cites the changed line.

## Proof

admin-ui smoke + CI. live-prove: n/a.
