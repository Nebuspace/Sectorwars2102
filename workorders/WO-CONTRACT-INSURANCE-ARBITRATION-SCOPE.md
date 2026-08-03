# WO-CONTRACT-INSURANCE-ARBITRATION-SCOPE

**Seat:** impl-sectorwars-cursor  
**Hub GO:** 2026-08-03T02:28:59Z  
**Branch:** `wo/CONTRACT-INSURANCE-ARBITRATION-SCOPE`  
**Base:** `origin/feat/expeditions-vista` @ `d0666a7b`

## Goal

Close the arbitration/dispute residual on an already-built 3-tier contract insurance system. Do **not** rebuild insure/premium/claim-offset.

## Scope (this residual)

1. **Limbo escalation** — ALL unresolvable Tier-1 filings set `escalated_to_admin=True` (contracts.md:402). E-I3 stays reason/metadata only.
2. **Player File Dispute UI** — `contractsAPI.dispute` + CTA on expired accepted Contract Board rows.
3. **Document seam no-ops** — cargo-manifest, issuer-unilateral-cancel, both-parties E-I3: explicit NOT-YET-BUILT comments.

## Out of scope

- Payout math / claim-offset / premium changes
- DETECT-REP / fence / mining-async
- Delivery-event log infra / reputation / cooldowns
- Hazard cargo-replacement / deadline-grace enrichment

## Accept

| # | Criterion |
|---|-----------|
| 1 | Unresolvable dispute always escalates; pytest pins low-value case |
| 2 | Player can File Dispute from Contract Board; vitest covers CTA |
| 3 | Seam no-ops documented NOT-YET-BUILT |
| 4 | Draft PR → feat/expeditions-vista |

## Proof

- `pytest` dispute escalation tests
- `vitest` ContractBoardVenue File Dispute
- `tsc` / player-client typecheck if available
