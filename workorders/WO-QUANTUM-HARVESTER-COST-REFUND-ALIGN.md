# WO-QUANTUM-HARVESTER-COST-REFUND-ALIGN

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/quantum-harvester-cost-refund-align`  
**Base:** `feat/expeditions-vista` @ `d11163ec` (hub tip pin `bb97b994` is ancestor)

## Goal

Align Quantum Field Harvester purchase cost to canon 50,000 cr, add 25% salvage refund on equipment uninstall, and correct the stale "slot not present" status line in `quantum-resources.md`.

## Blame finding (25k)

| Site | Introduced | Message |
|---|---|---|
| `EQUIPMENT_DEFINITIONS["quantum_harvester"].cost = 25000` | `03cdeb99` (2026-03-17) | mega-feat initial equipment slots — no canon-divergence note |
| `MODULE_DEFINITIONS` harvester `base_cost = 25000` | `94b6787b` (WO-SM-2) | "ported 1:1" from EQUIPMENT_DEFINITIONS |

**Verdict:** not a deliberate retune → docs-win → code → **50,000**.

## Scope

- `services/gameserver/src/services/ship_upgrade_service.py` — cost 50k (equipment + module Mk I base); `uninstall_equipment` refunds `int(cost × SALVAGE_FRACTION)`
- `services/gameserver/tests/unit/test_quantum_harvester_cost_refund.py` — install 50k + uninstall 25% pins
- `workorders/WO-QUANTUM-HARVESTER-COST-REFUND-ALIGN.md` (this file)
- **sw2102-docs** `FEATURES/galaxy/quantum-resources.md:130` — status line fix (local stage/commit only; **no push** without Rule-5 GO)

## Out of bounds

- No schema/migration
- No RBAC / heimdall-restart / DETECT-REP / fence
- No `sw2102-docs` push

## Accept

- [x] Unit test: install charges 50k
- [x] Unit test: removal refunds 25% of install cost (12,500)
- [x] Doc line :130 fixed (local)
- [x] Exclusive WT + JIT WO + PR → feat
