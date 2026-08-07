# WO-GS-PATROL-CITE-HONESTY

**Seat:** `impl-sectorwars-cursor`  
**Branch:** `wo/GS-PATROL-CITE-HONESTY`  
**Base:** `feat/expeditions-vista` @ `7cf3b6a3`

## Goal

Repair gameserver comment honesty where file:line cites drifted off their claimed anchors:

1. `nexus_generation_service.py` WO-GX1 `patrol_ships` scalar-int warning cited four "int() consumers" at `combat_service.py:3506`, `port_ownership_service.py:1792`, `admin.py:1495`, `admin_comprehensive.py:970` — none of those lines touch `patrol_ships` (ship-destroyed message / campaign month bounds / blank galaxy-stats line / player-create error log). Real readers: `combat_service.py:4425` + `port_ownership_service.py:2151` via `int(...)`; `admin.py:1569` + `admin_comprehensive.py:1127` via `.get`.
2. `fleet_service.py` deadlock-contract triad cited `trading.py:513` (market-observation docstring about rank-discounted prices) — station-first lock-order note lives at `trading.py:1016`. `planet_grid.py:245` and `auth.py:549` already land correctly; leave them.

## Scope

- `services/gameserver/src/services/nexus_generation_service.py`
- `services/gameserver/src/services/fleet_service.py`

## Out of bounds

- No behavior / schema / API changes
- Stay clear of K2/K2b/contraband/quantum/slipdrive/hangar/tow
- Do not "fix" `movement_service.py:1646` treating `patrol_ships` as a list (separate behavior question; not this honesty WO)
- enhanced_websocket BASIC_FOOD/TECHNOLOGY lists remain hub-banked WIRE-or-RETIRE

## Accept

1. Nexus WO-GX1 comment cites the four real `patrol_ships` readers above (and does not claim all four use `int()`)
2. Fleet deadlock triad cites `trading.py:1016` (with still-correct planet_grid/auth cites)
3. Diff is comment-only (plus this WO)

## Proof

- Tip greps: `int(defenses.get("patrol_ships"` → `:4425` / `:2151`; admin `.get("patrol_ships"` → `:1569` / `:1127`
- `trading.py:1016` = station-first lock-order comment on sell tax path
- live-prove: n/a (comment honesty; no shared-runtime / no DEPLOY-WINDOW)
