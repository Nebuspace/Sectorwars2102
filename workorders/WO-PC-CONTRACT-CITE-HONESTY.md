# WO-PC-CONTRACT-CITE-HONESTY

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/PC-CONTRACT-CITE-HONESTY`  
**Base:** `feat/expeditions-vista` @ `11a5836f`

## Goal

Repair player-client comment honesty where contracts / colonist-cap line cites drifted past reality:

1. `contract.ts` + `ContractBoardVenue.tsx` cited write-action response shapes at `contracts.py:550-558` / `:617-622` (past EOF — file is 518 lines) and other ranges that now land on lock-order / insure / dispute scaffolding, not response dicts. Real shapes live in `contract_service` return dicts; serialize is `contracts.py:50-86`.
2. `PostContractRequest` cite `:96-143` truncated mid-docstring; class ends at `:157`.
3. `PLAYER_POST_MIN_DEADLINE_HOURS` cited at `contract_service.py:81` (unrelated); defined at `contract_escrow_core.py:127`.
4. `INSURANCE_PREMIUM_PCT` / insure response attributed to `contract_service`; live in `contract_insurance.py`.
5. `GameDashboard.tsx` colonist-cap cite `planets.py:982` / `:993` (growth/terraform settle); real caps at `:1005-1009` / `:1010`.

## Scope

- `services/player-client/src/types/contract.ts`
- `services/player-client/src/components/spacedock/ContractBoardVenue.tsx`
- `services/player-client/src/components/pages/GameDashboard.tsx`

## Out of bounds

- No behavior / type / API changes
- Stay clear of K2/K2b/contraband/quantum/slipdrive/hangar/tow
- Do not touch gameserver runtime
- CC seat on HOLD (`awaiting-max-rbac-and-open5`) — player-client honesty only

## Accept

1. No player-client cite points at `contracts.py` past EOF or at the stale write-action ranges
2. `PLAYER_POST_MIN_DEADLINE_HOURS` cites `contract_escrow_core.py:127`
3. Colonist-cap cite lands on `planets.py:1005-1010`
4. Diff is comment-only (plus this WO)

## Proof

- `contracts.py` = 518 lines; `_serialize_contract` `:50-86`; write returns in `contract_service` as cited
- live-prove: n/a (comment honesty)
