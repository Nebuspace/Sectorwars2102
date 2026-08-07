# WO-ADM-DRONE-COMBAT-LOG-HONESTY

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/ADM-DRONE-COMBAT-LOG-HONESTY`  
**Base:** `feat/expeditions-vista` @ `4d0fa2f2`

## Goal

Repair admin-ui comment honesty: `DroneOperationsTab` claimed GET `/admin/drones/{id}` does not serialize `combat_log`, but `admin_drones.py:276-287` already does. Also repair verified drifted line citations in the same honesty class.

## Scope

- `services/admin-ui/src/components/combat/DroneOperationsTab.tsx` — combat_log serialization truth + cite `:276-287`
- `services/admin-ui/src/components/admin/PlayerDetailEditor.tsx` — emergency cite `:205` → `:224`
- `services/admin-ui/src/components/charts/FleetHealthReport.tsx` — HealthReportResponse cite `:67` → `:73`
- `services/admin-ui/src/components/pages/UsersManager.tsx` — users.py PUT/DELETE/password cites → `:123` / `:177` / `:209`

## Out of bounds

- No behavior / type / API changes
- Stay clear of K2/K2b/contraband/quantum/slipdrive/hangar/tow
- Do not touch gameserver runtime

## Accept

1. DroneOperationsTab comment states `combat_log` is serialized; cites `admin_drones.py:276-287`
2. PlayerDetailEditor / FleetHealthReport / UsersManager line cites match current decorators/classes
3. Diff is comment-only

## Proof

- Line cites re-checked against tip files
- `rg 'does not currently serialize'` in admin-ui → empty for this claim
- live-prove: n/a (comment honesty)
