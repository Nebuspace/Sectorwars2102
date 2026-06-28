# Legacy GalaxyGenerator Audit

Audit performed for the sw2102-bang cutover. Source of truth: the live tree at
`services/gameserver/`. All file:line refs are absolute paths within
`/Users/mrathbone/github/Nebuspace/Sectorwars2102/`.

## 1. Summary

`GalaxyGenerator` (defined in `services/gameserver/src/services/galaxy_service.py`)
is the legacy Python procedural-generation service for the Sectorwars universe.
It builds the relational hierarchy **Galaxy → Region → Zone → Cluster → Sector →
Warps / Warp Tunnels / Stations / Planets / Markets / MarketPrices** directly into
PostgreSQL via SQLAlchemy. It is invoked once per "new universe" from the admin
endpoint `POST /api/admin/galaxy/generate` and is also responsible for the
auto-creation of the Terran Space starter region during that same request (the
endpoint then hands off to a *separate* `nexus_generation_service` for Central
Nexus). A static helper `GalaxyGenerator.backfill_market_prices` is reused by a
one-off ops script. The class has ~1825 lines and embeds resource tables, name
generators, weighting tables, port-class distributions, and
faction/zone-based content rules.

## 2. Behaviors inventory

Every distinct piece of generation the class performs, with the file:line where
the logic lives in `services/gameserver/src/services/galaxy_service.py` unless
otherwise stated.

| # | Behavior | Location |
|---|----------|----------|
| 1 | Create `Galaxy` metadata row (statistics, density JSONB, max_sectors) | `galaxy_service.py:30-69` |
| 2 | Populate a region with clusters + sectors + warps + tunnels + ports + planets (orchestrator) | `galaxy_service.py:71-130` |
| 3 | Create N `Cluster` rows for a region with size jitter and naming `<Region> Cluster A/B/C…` | `galaxy_service.py:132-173` |
| 4 | Generate `Zone` rows: one Expanse zone (Central Nexus) OR three zones Federation/Border/Frontier (Terran/player regions) with policing & danger ratings | `galaxy_service.py:175-254` |
| 5 | Create `Sector` rows in a cluster with 3D coords, hazard/radiation, sector_type, zone assignment | `galaxy_service.py:256-315` |
| 6 | Build adjacency warps (`sector_warps` association table) between Manhattan-1 neighbors with bidirectional flag, turn cost, warp stability | `galaxy_service.py:317-352` |
| 7 | Enhanced warp-tunnel mesh: guarantee each sector ≥1 tunnel then add more, density-multiplied for Central Nexus (0.3x) vs others (1.0x) | `galaxy_service.py:354-402` |
| 8 | Create single `WarpTunnel` row (type, stability, turn cost, bidirectionality) | `galaxy_service.py:404-441` |
| 9 | Legacy long-distance random tunnel creator (still present, unused by new path) | `galaxy_service.py:443-479` |
| 10 | Populate sectors with `Station` rows by probability (5% Central Nexus, 15% others), with hard-coded commodities JSONB block (ore, organics, equipment, fuel, luxury_goods, gourmet_food, exotic_technology, colonists) | `galaxy_service.py:481-570` |
| 11 | Populate sectors with `Planet` rows by probability (10% Central Nexus, 25% others), pick PlanetType, set habitability, set initial population | `galaxy_service.py:572-618` |
| 12 | Create starter `Station` ("Terra Station", CLASS_1, TRADING) with full commodities + low tax `Market` row in a region's Sector 1 | `galaxy_service.py:620-697` |
| 13 | Create starter `Planet` ("New Earth", PlanetType.TERRAN, habitability 95, 8B population) in a region's Sector 1 | `galaxy_service.py:699-726` |
| 14 | Create `SpaceDock` Station (CLASS_11 Shipyard) in sector 10 of each region — sells genesis devices, drones, mines, ship upgrades; seeds `MarketPrice` rows for 7 commodities | `galaxy_service.py:728-857` |
| 15 | Create `MarketPrice` rows from a station's commodities JSONB with buy/sell spread logic (buys+sells / buys-only / sells-only) | `galaxy_service.py:859-907` |
| 16 | `GalaxyGenerator.backfill_market_prices` static method: scan all stations missing `MarketPrice` rows and recreate them | `galaxy_service.py:909-976` |
| 17 | `_ensure_region_starter_sector`: force region's Sector 1 to be safe (hazard=0, radiation=0, STANDARD type), guarantee station + planet exist | `galaxy_service.py:978-1013` |
| 18 | DEPRECATED `_ensure_starter_sector` (global Sector 1, pre-multi-regional, still in file, logs a warning) | `galaxy_service.py:1015-1140` |
| 19 | `_add_special_sectors`: ~2% of sectors get converted to BLACK_HOLE/NEBULA/ASTEROID_FIELD/STAR_CLUSTER/VOID/WORMHOLE; wormholes spawn one-way tunnels to distant sector | `galaxy_service.py:1142-1185` |
| 20 | DEPRECATED `_create_terran_space_region`: creates Terran Space region + assigns sectors 1-300; **now replaced by inline code in the admin route** | `galaxy_service.py:1187-1246` |
| 21 | `_update_galaxy_statistics`: count stations/planets/tunnels, update Galaxy JSONB density/statistics | `galaxy_service.py:1248-1272` |
| 22 | Cluster-type selection per region (Central Nexus gets MILITARY/TRADE/POPULATION; Terran gets POPULATION/TRADE/STANDARD; player regions get balanced mix) | `galaxy_service.py:1275-1287` |
| 23 | Sector-type selection per cluster type (base nebula/asteroid/void + extras per cluster type) | `galaxy_service.py:1289-1308` |
| 24 | 3D coordinate generation per cluster — tight for small clusters, sqrt-grid spread for large clusters; collision-avoid via `sector_grid` | `galaxy_service.py:1310-1350` |
| 25 | Radiation/hazard lookup tables by sector type | `galaxy_service.py:1352-1382` |
| 26 | Sector-resource generation (2-4 resources, quality LOW/STANDARD/HIGH, regen rate) | `galaxy_service.py:1384-1403` |
| 27 | Turn-cost + warp-stability calculators between sectors (hazard penalties, type penalties) | `galaxy_service.py:1405-1441` |
| 28 | 3D Euclidean distance helper | `galaxy_service.py:1443-1447` |
| 29 | Warp tunnel type chooser (weighted STANDARD/QUANTUM/ANCIENT/ARTIFICIAL/UNSTABLE/ONE_WAY) + stability + turn cost helpers | `galaxy_service.py:1449-1496` |
| 30 | Station type chooser by cluster type, plus 30% black-market chance in FRONTIER zones | `galaxy_service.py:1498-1521` |
| 31 | Station class chooser by zone (FEDERATION/BORDER/FRONTIER weights) + cluster bonuses; Sector 1 always CLASS_0 | `galaxy_service.py:1523-1571` |
| 32 | Faction assignment per zone (terran_federation, mercantile_guild, frontier_coalition, etc.) | `galaxy_service.py:1573-1591` |
| 33 | Economic-specialization / resource-availability / resource-price tables per StationType | `galaxy_service.py:1593-1685` |
| 34 | Planet-type selection by sector special type + zone weights | `galaxy_service.py:1687-1738` |
| 35 | Habitability score table + planet resource table by planet type, with 5% chance of QUANTUM_COMPONENTS exotic | `galaxy_service.py:1740-1797` |
| 36 | Max-population table by planet type, scaled by habitability | `galaxy_service.py:1799-1824` |
| 37 | Procedural planet-name generator (prefix + element + suffix) | `galaxy_service.py:1826-1844` |
| 38 | Hardcoded "Earth Station" guarantee in Sector 1 — **lives in the admin route**, not in `GalaxyGenerator` | `services/gameserver/src/api/routes/admin.py:1077-1101` |
| 39 | Inline Terran Space `Region` row creation (governance, tax, theme, language_pack, etc.) — **lives in the admin route** | `services/gameserver/src/api/routes/admin.py:1042-1075` |
| 40 | Auto-trigger of `nexus_generation_service.generate_central_nexus()` after the Terran Space block (separate service, not part of `GalaxyGenerator`) | `services/gameserver/src/api/routes/admin.py:1111-1128` |

Companion class `GalaxyService` (lightweight wrapper, separate from the
generator) lives at `services/gameserver/src/services/galaxy_service.py:1847-1882`
and exposes `create_new_galaxy`, `get_galaxy_by_id`, `get_default_galaxy`,
`get_sector_by_id`, `get_adjacent_sectors`, `calculate_path`. Only
`create_new_galaxy` delegates to `GalaxyGenerator`; the read methods are
independent. See §3 for caller analysis.

## 3. Callers

Every import or instantiation site for `GalaxyGenerator`, `GalaxyService`, or
the `galaxy_service` module:

| Caller | File:line | What it does |
|--------|-----------|--------------|
| Admin route `POST /api/admin/galaxy/generate` | `services/gameserver/src/api/routes/admin.py:1019, 1021, 1033, 1072` | Imports `GalaxyGenerator`, creates galaxy metadata, calls `generate_region_content` for Terran Space |
| One-off ops script | `services/gameserver/backfill_market_prices.py:21, 28` | Imports `GalaxyGenerator`, calls static `backfill_market_prices(db)` |
| `GalaxyService.create_new_galaxy` | `services/gameserver/src/services/galaxy_service.py:1855-1856` | Wrapper instantiates `GalaxyGenerator(db)` and calls `generate_galaxy` — **but no live caller invokes `create_new_galaxy`** (see below) |
| Admin route module `admin_comprehensive.py` | `services/gameserver/src/api/routes/admin_comprehensive.py:29` | Imports `GalaxyService` — **unused import** (verified: 1 occurrence in file, zero usages of the symbol) |

Verification commands run:
- `grep -rn "GalaxyGenerator\|generate_galaxy\|galaxy_service\|GalaxyService" services/gameserver/`
- `grep -rn "GalaxyGenerator\|generate_galaxy" services/` (broader sweep, no extra hits)

No other Python file (services, routes, tests, scripts, migrations) imports
either symbol. `GalaxyService.create_new_galaxy` is dead code: zero callers in
the codebase.

## 4. Endpoints exposed

Endpoints in `services/gameserver/src/api/routes/admin.py` that depend on
`GalaxyGenerator` directly, or that operate on the data it produces:

| Method | Path | File:line | Auth | Triggers GalaxyGenerator? |
|--------|------|-----------|------|---------------------------|
| POST | `/api/admin/galaxy/generate` | `admin.py:1001-1151` | `get_current_admin` (admin JWT) | YES — instantiates `GalaxyGenerator`, calls `generate_galaxy` + `generate_region_content`, then triggers `nexus_generation_service.generate_central_nexus` |
| GET | `/api/admin/galaxy` | `admin.py:890-998` | `get_current_admin` | NO — read-only fetch of `Galaxy` row (must keep) |
| DELETE | `/api/admin/galaxy/clear` | `admin.py:1342-1370` | `get_current_admin` | NO — wipes ships/players/stations/planets/warp_tunnels/sectors/clusters/regions/galaxy (must keep; will be called before re-running new generator) |
| POST | `/api/admin/galaxy/fix-statistics` | `admin.py:1372-1425` | `get_current_admin` | NO — JSONB stats migration helper, references Galaxy + Station only |
| DELETE | `/api/admin/galaxy/{galaxy_id}` | `admin.py:1427-1447` | `get_current_admin` | NO — delete a single Galaxy row |

Also nominally related (in scope of "galaxy generation" but **separate** services):
- POST `/api/v1/nexus/generate` — `services/gameserver/src/api/routes/nexus.py:60` — Central Nexus generation, uses `nexus_generation_service`, NOT `GalaxyGenerator`. The admin `galaxy/generate` route also calls into this service inline. Stays for now.
- The deprecated zone-based generator endpoint was already removed; the comment block remains at `services/gameserver/src/api/routes/admin_enhanced.py:71-73`.

**Frontend callers** of `POST /api/admin/galaxy/generate`:
- `services/admin-ui/src/contexts/AdminContext.tsx:380-409` — `generateGalaxy(name, numSectors, config)` posts to `/admin/galaxy/generate`.
- `services/admin-ui/src/components/pages/Universe.tsx:15, 130, 157, 795-800` — calls `generateGalaxy` from the UniverseManager "Generate" and "Regenerate Galaxy" buttons.
- `services/admin-ui/src/components/pages/UniverseManager.tsx:22, 102, 135` — alternate UniverseManager page.
- Also referenced: `services/admin-ui/src/contexts/AdminContext.tsx:413-432` posts to `/admin/galaxy/generate-enhanced` — **this endpoint does not exist** in the backend (verified by grep); dead UI path.

There is no `services/admin-ui/src/components/universe/` UI calling the generator directly; `UniverseEditor.tsx` only reads via `useAdmin()`.

## 5. Tests touching it

Verified by `grep -rn "galaxy\|Galaxy\|GalaxyGenerator\|generate_galaxy" services/gameserver/tests/`.

Result: **zero hits**. No unit, integration, or security test file references
`GalaxyGenerator`, `GalaxyService`, the `galaxy_service` module, the
`/admin/galaxy/generate` endpoint, or any Galaxy/Sector/Region model in the
gameserver test tree. The full test list (`tests/conftest.py`,
`tests/unit/`, `tests/integration/api/`, `tests/security/`) covers auth,
governance, central nexus (separate service), docking turns, factions, status,
and admin endpoints, but none exercise galaxy generation.

E2E tests in `e2e_tests/` were not part of this audit (the instructions
scoped this to `services/gameserver/tests/`). A follow-up check there is
recommended before final cutover, but no Python test breakage is expected from
removing `GalaxyGenerator`.

## 6. Database migrations relevant

All galaxy-content tables were created in the initial schema migration
`services/gameserver/alembic/versions/c138b33baec4_initial_schema_with_stations_terminology.py`:

| Table | Migration line |
|-------|----------------|
| `galaxies` | `c138b33baec4_…:84` |
| `planets` | `c138b33baec4_…:157` |
| `planet_formations` | `c138b33baec4_…:735` |
| `player_planets` | `c138b33baec4_…:763` |
| `regions` | `c138b33baec4_…:783` |
| `clusters` | `c138b33baec4_…:1107` |
| `zones` | `c138b33baec4_…:1325` |
| `sectors` | `c138b33baec4_…:1358` |
| `sector_warps` (assoc) | `c138b33baec4_…:1531` |
| `stations` | `c138b33baec4_…:1542` |
| `warp_tunnels` | `c138b33baec4_…:1582` |
| `markets` | `c138b33baec4_…:1886` |
| `market_prices` | `c138b33baec4_…:1861` |
| `player_stations` | `c138b33baec4_…:1906` |

Subsequent migrations that touch galaxy-related tables (do **not** create the
core schema, only add columns / fix data):

- `e86cb8130b5b_rename_all_port_columns_to_station.py` — renames port→station columns
- `5f5a988bdbb1_add_current_port_id_and_current_planet_.py` — player position fields
- `b2c3d4e5f6a7_add_terraforming_columns_to_planets.py`
- `a1b2c3d4e5f6_add_planet_morale_and_siege_turns.py`
- `a3f7c2d91e54_add_genesis_formation_columns_to_planets.py`
- `dbbfad27a7ef_change_planet_population_columns_to_.py`
- `e3f4a5b6c7d8_add_citadel_fields_to_planets.py`
- `c1d2e3f4a5b6_merge_genesis_and_terraforming_heads.py`
- `7c2e91d6f4b8_add_special_formations_table.py`

**Migration plan for cutover**: every table above is still required by the
runtime (Trading, Combat, Movement, Citadel, Planet, Station services all
depend on them). **No migration drops are needed** when removing
`GalaxyGenerator`. The new `sw2102-bang`–driven generation should produce rows
in the same tables with the same column shapes. Only **new** migrations would
be required if `sw2102-bang` introduces additional columns (e.g., richer
metadata) — that is out of scope for the audit but flagged here.

## 7. Removal plan (Phase 4 cutover PR)

Ordered list of deletions / edits. Order chosen so the codebase is never in a
broken-import state mid-PR.

1. **Replace the body of `POST /api/admin/galaxy/generate`** at
   `services/gameserver/src/api/routes/admin.py:1001-1151`. Remove:
   - The `from src.services.galaxy_service import GalaxyGenerator` import (line 1019)
   - The `generator = GalaxyGenerator(db)` instantiation (line 1021)
   - The `generator.generate_galaxy(...)` call (line 1033)
   - The `generator.generate_region_content(...)` call (line 1072)
   - The hardcoded Terran Space `Region(...)` block (lines 1042-1075) — replaced by sw2102-bang region setup
   - The "Earth Station" inline guarantee (lines 1077-1101) — sw2102-bang should provide it
   - Keep the request schema `GalaxyGenerateRequest` (lines 31-37) for now unless sw2102-bang prescribes a new shape
   - Keep / re-target the Central Nexus call (lines 1111-1128) per cutover decisions
2. **Delete the entire `GalaxyGenerator` class**:
   `services/gameserver/src/services/galaxy_service.py:21-1844`.
3. **Delete the `GalaxyService` class** too (its only generator-touching
   method is dead, and its read helpers are unused):
   `services/gameserver/src/services/galaxy_service.py:1847-1882`.
   Verify by grep that no other file imports `GalaxyService` after step 4.
4. **Remove the orphaned import in `admin_comprehensive.py`**:
   delete line `services/gameserver/src/api/routes/admin_comprehensive.py:29`
   (`from src.services.galaxy_service import GalaxyService`).
5. **Delete the entire file** `services/gameserver/src/services/galaxy_service.py`
   once steps 2-4 leave it empty (verify imports of the *module path* are gone:
   `grep -rn "galaxy_service" services/gameserver/src/`).
6. **Delete the ops script** `services/gameserver/backfill_market_prices.py`
   — it depends on `GalaxyGenerator.backfill_market_prices`. If the
   `MarketPrice` backfill behavior is still needed post-cutover, port it into
   a standalone script that does not reference `GalaxyGenerator`. (The logic
   is self-contained at `galaxy_service.py:909-976` and can be lifted as-is.)
7. **Admin UI cleanup** (optional but recommended in same PR):
   - Remove the dead `generateEnhancedGalaxy` path in
     `services/admin-ui/src/contexts/AdminContext.tsx:412-432` (calls a
     non-existent backend endpoint `/admin/galaxy/generate-enhanced`).
   - Re-validate `generateGalaxy` payload shape matches new sw2102-bang
     contract; update `Universe.tsx` and `UniverseManager.tsx` if needed.
8. **Run grep one more time** to confirm zero remaining references:
   `grep -rn "GalaxyGenerator\|galaxy_service\|GalaxyService" services/gameserver/`.

## 8. Risks of removal

Top risks to verify before merging the cutover PR:

1. **`backfill_market_prices.py` (HIGH)** — a real ops script lives at
   `services/gameserver/backfill_market_prices.py` that imports
   `GalaxyGenerator` to fix stations whose `MarketPrice` rows are missing.
   If any production / dev galaxy has been generated via the legacy path
   recently, ops may need to run this script post-cutover. **Mitigation**:
   either (a) run it before deleting, (b) port the static method into a
   standalone script in the same PR, or (c) confirm sw2102-bang's output
   never produces a station without matching `MarketPrice` rows.

2. **Terran Space + Sector 1 Earth Station guarantees (HIGH)** — the
   first-login flow / starter-ship flow assumes Sector 1 has both a station
   and a planet, that the station is "Earth Station" with adequate stock,
   and that hazard/radiation in Sector 1 are zero. These guarantees are
   currently enforced in two places: `galaxy_service.py:978-1013` (region
   Sector 1) and `admin.py:1077-1101` (Earth Station). Cutover must ensure
   sw2102-bang produces equivalent invariants, or first-login will break.
   `services/gameserver/src/services/first_login_service.py` should be
   re-read alongside this risk.

3. **SpaceDock dependency for genesis / drones / mines (HIGH)** — the
   `_create_spacedock_for_region` method (`galaxy_service.py:728-857`)
   creates a CLASS_11 Shipyard in sector 10 of each region that sells
   genesis devices, combat / defense / mining drones, and limpet mines.
   Downstream services (`genesis_service.py`, `drone_service.py`,
   `terraforming_service.py`) and the SpaceDock UI assume at least one
   such station exists per region. If sw2102-bang does not emit a
   functionally equivalent SpaceDock with the right `services` JSONB flags
   (`genesis_dealer`, `mine_dealer`, `drone_shop`, `ship_dealer`,
   `ship_upgrades`, `insurance`, etc.), those subsystems will silently
   stop selling. Worth tracing every consumer of
   `Station.services['genesis_dealer'] == True` before merge.

Additional lower-severity risks worth checking but not in the top three:

- **Unused `GalaxyService` import** in `admin_comprehensive.py:29` is
  harmless today but blocks deletion if missed.
- **`start.sh` does NOT auto-generate a galaxy** (verified — it only runs
  migrations + table creation). No init script depends on `GalaxyGenerator`.
- **No cron / scheduled task** references `GalaxyGenerator` (verified by
  grep across `services/gameserver/`).
- **`UniverseEditor.tsx`** is read-only via `useAdmin()`; it does not call
  the generator, so it won't break, but any new sw2102-bang fields not
  exposed via `/api/admin/galaxy` will simply render as empty in the UI.
- **`generateEnhancedGalaxy` UI path** points at a backend endpoint that
  does not exist (`/admin/galaxy/generate-enhanced`). Already dead — just
  confirms admin UI tolerates removal.
- **Central Nexus generation** is handled by a separate service
  (`nexus_generation_service`); it is currently chained from the legacy
  endpoint at `admin.py:1111-1128`. Cutover needs an explicit decision:
  keep chaining, move into sw2102-bang, or expose as a separate post-cutover
  call.

---

**Audit scope notes**

- All file/line references verified by direct read or exact-match grep at
  audit time.
- `e2e_tests/` was not searched (out of scope per instructions). A follow-up
  grep there is recommended before merge.
- `sw2102-bang/` repo was deliberately untouched per instructions.
