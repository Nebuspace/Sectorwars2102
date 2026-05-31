# Bang Universe JSON → Gameserver Schema Map

**Status**: DRAFT — produced by Schema Mapper subagent for the Translator Author subagent
**Owner**: Claude (schema-mapper pass) / Samantha (review)
**Last revised**: 2026-05-31
**Inputs read**:
- `sw2102-bang/src/types.ts` (v1.3.0 schema, lines 1–615)
- `sw2102-bang/src/serialize.ts` (JSON round-trip; no field remapping)
- `Sectorwars2102/services/gameserver/src/models/galaxy.py`, `region.py`, `cluster.py`, `sector.py`, `warp_tunnel.py`, `station.py`, `planet.py`, `special_formation.py`, `faction.py`, `zone.py`

---

## 1. Summary

Bang emits a JSON Universe of `{version, seed, totalSectors, sectors{}, warps[], specialLocations[], fedspaceSectors[], clusters[], specialFormations[], npcRosters[], config, createdAt}`. The gameserver canonical schema is the SQLAlchemy graph rooted at `Galaxy → Region → Cluster → Sector`, with `Planet`, `Station` (gameserver name for bang's `Port`), `WarpTunnel`, and `SpecialFormation` hanging off it. The two shapes line up cleanly at the macro level but diverge in three structural ways the translator must absorb:

1. **Integer sector IDs vs UUID PKs.** Bang keys everything (warps, formations, rosters, special locations) by integer `sectorId` 1..N. The gameserver PKs every row with a `uuid.uuid4()` UUID and references sectors *across cluster/region scope* via `Sector.id` (UUID). The translator must build and hold an in-memory `int → UUID` map for sectors *and* clusters and rewrite every cross-reference.
2. **Bidirectional warps as one `Warp` row vs two `sector_warps` rows.** Bang emits one `Warp{from,to,oneWay}`; gameserver's `sector_warps` association table stores per-direction rows with an `is_bidirectional` flag. Per the integration plan, sector-adjacency warps go into `sector_warps` (NOT `warp_tunnels`, which is for premium/quantum/artificial tunnels). The translator must expand each two-way warp into a single row with `is_bidirectional=true` and each one-way warp into one row with `is_bidirectional=false`.
3. **`Port` ≠ `Station` 1:1.** Bang's `Port` uses TW2002 classes 0..8 and a 9-commodity dict; gameserver's `Station` extends to classes 0..11, has 8 named commodity columns, an enum `StationType`, services, defenses, trader-personality, and reputation thresholds. The translator must default these enrichment fields and use class→type mapping (e.g., class 1 mining → `StationType.MINING`).

There is **no `Nebula` table** on the gameserver — sector-level nebulae must collapse onto `Sector.type=NEBULA` + `Sector.special_features` JSONB. There is **no `NPCCharacter`/`NPCBarracks`/`OutlawBase`** table yet — `npcRosters` are flagged for either a new migration or a deferred runtime materialization (open question Q3).

Galaxy + Region rows mostly **do not come from bang** — they are operator-set per the integration plan. The translator receives a pre-created `region_id` and writes everything underneath it.

---

## 2. Per-entity mapping tables

### 2.1 Universe (top-level)

The Universe envelope is metadata. Most fields land on the `Galaxy` row's `bang_snapshot` JSONB blob (audit trail) plus a few stamped columns. There is no 1:1 Universe→Region row.

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `version` | string ("1.3.0") | `Galaxy.bang_version` (NEW; per integration plan) | TEXT | Stamp for audit. Not on existing Galaxy schema — translator adds via Job Model Author's Alembic migration. |
| `seed` | number (uint64 as JS number) | `Galaxy.bang_seed` (NEW) | BIGINT | Bang treats as positive int; serialize as BIGINT to avoid JS-number precision loss. |
| `totalSectors` | number | `Galaxy.max_sectors` + `Galaxy.statistics.total_sectors` | INTEGER / JSONB | Both columns get updated; `max_sectors` is the hard cap, `statistics` is the live count. |
| `sectors` | `Record<string, Sector>` | (iterated → `sectors` table rows) | — | JSON object keys are stringified sector IDs; translator iterates `Object.entries(sectors)` and emits Sector rows. |
| `warps` | `Warp[]` | (iterated → `sector_warps` rows) | — | See §2.4. |
| `specialLocations` | `SpecialLocation[]` | (decorated onto Sector rows + stamped on Region) | — | See §2.8. |
| `fedspaceSectors` | `number[]` | (decorated onto Sector + Cluster rows) | — | Translator flips `Sector.security_level=10` and `Cluster.special_features+=['fedspace']` for every id in this list. |
| `config` | `BigBangConfig` | `Galaxy.bang_snapshot.config` (JSONB) | JSONB | Stored verbatim for reproducibility. Several individual fields (density %, twoWayWarps %, etc.) also shadow into `Galaxy.density` JSONB for the existing UI to read. |
| `createdAt` | string (ISO-8601) | `Galaxy.bang_generated_at` (NEW) | TIMESTAMPTZ | Parse via `datetime.fromisoformat()`; SQLAlchemy `DateTime(timezone=True)` accepts the resulting tz-aware datetime. |
| `clusters` | `Cluster[]?` | (iterated → `clusters` rows) | — | Optional in schema — but the translator should refuse to import if absent for v1.3.0, since the gameserver Sector.cluster_id is NOT NULL. |
| `specialFormations` | `SpecialFormation[]?` | (iterated → `special_formations` rows) | — | Optional in v1.3.0. Translator emits zero rows if absent. |
| `npcRosters` | `NPCRoster[]?` | (open — see Q3) | — | Either persisted to a new `npc_rosters` staging table or held in `Galaxy.bang_snapshot.npc_rosters` for runtime materialization. Decision pending. |

Whole-blob persistence: the integration plan ("`bang_snapshot` JSONB in Galaxy row") means the **entire raw Universe JSON is also stored** on Galaxy for reproducibility/debugging.

### 2.2 Cluster

Bang's `Cluster` rows partition the sector-number range. They map almost cleanly to `clusters` table rows.

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `id` | number (1..K) | (translator-built UUID; recorded in `int→UUID` map) | — | `cluster.id = uuid4()`. Bang's int id is NOT persisted; it lives only in the translator's in-memory map to wire `Sector.cluster_id`, `SpecialFormation.cluster_id` references, and roster `hostSectorId` resolution. |
| `name` | string | `Cluster.name` | VARCHAR(100) | Direct copy. AI-generated name; bang guarantees UNIQUE within region (per ADR-0044). |
| `type` | `ClusterType` (8 enum values) | `Cluster.type` | Enum `cluster_type` | **Values match 1:1** — both enums use the same UPPER_SNAKE_CASE strings (STANDARD, RESOURCE_RICH, POPULATION_CENTER, TRADE_HUB, MILITARY_ZONE, FRONTIER_OUTPOST, CONTESTED, SPECIAL_INTEREST). Direct enum assignment. |
| `sectorRangeStart` | number | `Cluster.stats["sector_range_start"]` (JSONB) | JSONB key | No dedicated column; stash in the `stats` JSONB so the translator can later reconstruct which sectors live in which cluster without an extra query. |
| `sectorRangeEnd` | number | `Cluster.stats["sector_range_end"]` (JSONB) | JSONB key | Same as above. |
| `coords.x` | number | `Cluster.x_coord` | INTEGER | Direct. |
| `coords.y` | number | `Cluster.y_coord` | INTEGER | Direct. |
| `coords.z` | number | `Cluster.z_coord` | INTEGER | Direct. |
| `warpStability` | number (0..1) | `Cluster.warp_stability` | FLOAT | Direct. |
| `economicValue` | number (0..100) | `Cluster.economic_value` | INTEGER | Direct. |
| `recommendedShipClass` | `RecommendedShipClass` (6 values) | `Cluster.recommended_ship_class` | VARCHAR (free-form) | Gameserver column is `String`, not an enum; bang's enum values (`any`, `light_freighter`, `medium_freighter`, `heavy_freighter`, `fighter`, `corvette`) are stored verbatim. |
| `sectorCount` | number | `Cluster.sector_count` | INTEGER | Direct. |
| `maxWarps` | number (NEW v1.3.0) | `Cluster.stats["max_warps"]` (JSONB) | JSONB key | No dedicated column; stash in `stats` JSONB. Per-cluster warp cap is informational once warps are stamped on sectors. |
| `islandGroupId` | number? (NEW v1.3.0) | `Cluster.stats["island_group_id"]` (JSONB) | JSONB key | Optional; stash in `stats`. Drives ARCHIPELAGO formation cross-validation. |
| `isDiscovered` | boolean | `Cluster.is_discovered` | BOOLEAN | Direct. |
| `isHidden` | boolean | `Cluster.is_hidden` | BOOLEAN | Direct. |

### 2.3 Sector

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `id` | number (1..N) | `Sector.sector_id` AND `Sector.sector_number` | INTEGER (unique) | Bang's int id → both columns. Gameserver PK `Sector.id` is a freshly-minted UUID. Translator records the `int → UUID` map. |
| `position.x` | number | `Sector.x_coord` | INTEGER | Direct (already in POSITION_SCALE = 10000 integer range). |
| `position.y` | number | `Sector.y_coord` | INTEGER | Direct. |
| `position.z` | number | `Sector.z_coord` | INTEGER | Direct. |
| `warps` | `number[]` | (handled via `Universe.warps[]` → sector_warps) | — | Per-sector warp list is **redundant** with the top-level `warps[]` array. Translator drops it; uses top-level array as the canonical edge list. |
| `port` | `Port \| null` | (emits `Station` row in `stations`) | — | See §2.5. `port: null` → no Station row. |
| `planets` | `Planet[]` | (emits `Planet` rows in `planets`) | — | See §2.6. |
| `navHazards` | `NavHazard[]` | `Sector.nav_hazards` (JSONB) | JSONB | Bang emits typed objects; translator stores as-is in the JSONB column. Note: bang's walking-skeleton emits empty list, so this is mostly a no-op until follow-up PR. |
| `nebula` | `Nebula \| null` | `Sector.type = NEBULA` + `Sector.special_features += ['nebula_<type>', 'nebula_density_<n>']` | Enum + ARRAY(String) | See §2.7. There is no `nebulae` table — they collapse onto the host Sector. |
| `beacon` | `string \| null` | `Sector.nav_beacons` (JSONB array) | JSONB | If non-null, translator pushes `{"text": <beacon>}` onto `nav_beacons`. |
| `explored` | boolean | `Sector.is_discovered` | BOOLEAN | Direct. |
| — | — | `Sector.name` | VARCHAR(100) NOT NULL | **Compute**: translator builds `f"Sector {id}"` for plain sectors, or uses Special Location naming (Terra, Stardock, …) when the sector is a special-location host. |
| — | — | `Sector.cluster_id` | UUID NOT NULL FK | **Compute**: look up cluster UUID via `int → UUID` map using each cluster's `sectorRangeStart..End`. |
| — | — | `Sector.region_id` | UUID FK | **Compute**: translator receives the `region_id` from the orchestrator (per integration plan) and stamps every Sector row with it. |
| — | — | `Sector.zone_id` | UUID FK nullable | **Compute**: translator splits sectors into Federation/Border/Frontier zones by thirds (per `zone.py` docstring "DEFAULT for player regions"). |
| — | — | `Sector.type` | Enum `sector_type` | **Compute**: default `STANDARD` unless nebula present (then `NEBULA`). |
| — | — | `Sector.security_level` | INTEGER (1..10) | **Compute**: 10 for sectors in `fedspaceSectors[]`; else derived from cluster.type (TRADE_HUB→7, FRONTIER_OUTPOST→3, etc.). |
| — | — | `Sector.hazard_level` | INTEGER (0..10) | **Compute**: derived from cluster.type + nav-hazard count. |

### 2.4 Warp

Bang's edge model: `{from, to, oneWay}`. Gameserver's edge model: `sector_warps` association table with `(source, destination, is_bidirectional, turn_cost, warp_stability)` columns.

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `from` | number | `sector_warps.source_sector_id` | UUID FK | Translator resolves int → UUID via the sector map. |
| `to` | number | `sector_warps.destination_sector_id` | UUID FK | Same. |
| `oneWay` | boolean | `sector_warps.is_bidirectional` | BOOLEAN | **Inverted**: `is_bidirectional = !oneWay`. |
| — | — | `sector_warps.turn_cost` | INTEGER | **Compute**: default 1 (per existing schema default). |
| — | — | `sector_warps.warp_stability` | FLOAT | **Compute**: inherit from `Cluster.warp_stability` of the source sector's cluster (or 1.0 fallback). |
| — | — | `sector_warps.created_at` | TIMESTAMPTZ | **Compute**: DB default `func.now()`. |

**NOT WarpTunnel**: the `warp_tunnels` table is reserved for premium/quantum/artificial tunnels (per `WarpTunnelType.NATURAL|ARTIFICIAL|QUANTUM|…`). Bang's standard sector-adjacency warps go into `sector_warps`. Cross-region warp gates remain gameserver-managed and go into `warp_tunnels` afterwards.

### 2.5 Port → Station

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `name` | string | `Station.name` | VARCHAR(100) | Direct. |
| `class` | number (0..8; sentinel 0 = SpaceDock) | `Station.station_class` | Enum `station_class` (CLASS_0..CLASS_11) | Direct map: bang 0..8 → `StationClass.CLASS_0..CLASS_8`. **NB**: gameserver enum extends to CLASS_11 (Luxury, Advanced Tech) which bang doesn't emit. |
| `commodities` | `Record<string, PortCommodityState>` | `Station.commodities` (JSONB) | JSONB | Per-commodity translation: bang `{action, quantity, capacity, regenRate}` → gameserver `{quantity, capacity, base_price, current_price, production_rate, price_variance, buys, sells}`. `action: 'B' → buys: true, sells: false` (and vice versa); `regenRate → production_rate`; `base_price` / `price_variance` come from `GenerationRequest.commodityCatalog` if provided, else gameserver defaults. **9-commodity wire vs 8-commodity gameserver default** — gameserver default dict has `[ore, organics, equipment, fuel, luxury_goods, gourmet_food, exotic_technology, colonists]`; bang's 9-commodity catalog list is bang-side per ADR-0062 E-D2 — **open question Q1**. |
| `isSpaceDock` | boolean? | `Station.is_spacedock` (NEW column needed) | BOOLEAN | **Column does NOT currently exist** on `Station`. Per bang's docstring (`types.ts:81-89`) the translator routes via `Station.is_spacedock = true`. Open question Q2 — needs Alembic migration. |
| — | — | `Station.sector_id` | INTEGER | **Compute**: bang sector int id (the host sector's number). |
| — | — | `Station.sector_uuid` | UUID FK | **Compute**: resolve via `int → UUID` sector map. |
| — | — | `Station.type` | Enum `station_type` | **Compute**: derived from class (CLASS_1→MINING, CLASS_2→AGRICULTURAL→???, …). No 1:1 enum mapping; translator needs a class→StationType lookup table. (StationType has 10 values; bang's 9 classes don't perfectly partition.) |
| — | — | `Station.status` | Enum | **Compute**: default `OPERATIONAL`. |
| — | — | `Station.size` | INTEGER (1..10) | **Compute**: default 5 (or derive from class). |
| — | — | `Station.faction_affiliation` | VARCHAR | **Compute**: derive from cluster's controlling_faction; nullable. |
| — | — | `Station.trader_personality` | JSONB | **Compute**: derive `type` from cluster.type (TRADE_HUB→LUXURY, FRONTIER_OUTPOST→FRONTIER, etc.). |
| — | — | `Station.services`, `service_prices`, `defenses`, `ownership`, `acquisition_requirements` | JSONB | **Compute**: gameserver defaults from `Station.__init__` server-side dict. |
| — | — | `Station.region_id` | UUID FK | **Compute**: orchestrator-supplied. |

### 2.6 Planet

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `name` | string | `Planet.name` | VARCHAR(100) | Direct. |
| `type` | `PlanetType` (6 bang values) | `Planet.type` | Enum `planet_type` (12 gameserver values) | **Lossy mapping**: bang's `barren→BARREN`, `earth→TERRAN`, `mountainous→MOUNTAINOUS`, `oceanic→OCEANIC`, `glacial→ICE`, `volcanic→VOLCANIC`. Gameserver's DESERT/GAS_GIANT/JUNGLE/ARCTIC/TROPICAL/ARTIFICIAL never appear from bang. |
| `owner` | string \| null | `Planet.owner_id` (open question) | UUID FK nullable | Bang emits string (e.g., faction code or "fed"); gameserver column is UUID FK to `players.id`. For seeded/NPC ownership, the translator likely sets `owner_id = NULL` and stamps `Planet.controlling_faction` in a follow-up or via `Planet.economy` JSONB. Open question Q4. |
| `habitabilityScore` | number (0..100) | `Planet.habitability_score` | INTEGER | Direct. |
| `maxPopulation` | number | `Planet.max_population` | BIGINT | Direct. (Per bang docstring: `habitabilityScore × 1000`.) |
| `maxColonists` | number | `Planet.max_colonists` | INTEGER | Direct. (Per bang docstring: 1000 = L1 Outpost cap.) |
| `ore` | number | `Planet.fuel_ore` | INTEGER | **Rename**: bang's `ore` → gameserver `fuel_ore`. Watch this — easy translator bug. |
| `organics` | number | `Planet.organics` | INTEGER | Direct. |
| `equipment` | number | `Planet.equipment` | INTEGER | Direct. |
| `colonists` | number | `Planet.colonists` | INTEGER | Direct. |
| `citadel` | `Citadel \| null` | (fields exploded onto Planet row) | — | See sub-table. |
| — | — | `Planet.sector_id` | INTEGER | **Compute**: host sector int. |
| — | — | `Planet.sector_uuid` | UUID FK | **Compute**: via map. |
| — | — | `Planet.status` | Enum | **Compute**: `COLONIZED` if `citadel != null`; `HABITABLE` if `habitabilityScore ≥ 40`; else `UNINHABITABLE`. |
| — | — | `Planet.size`, `Planet.position`, `Planet.gravity`, `Planet.atmosphere`, `Planet.temperature`, `Planet.water_coverage` | various | **Compute**: gameserver defaults. Bang doesn't emit physical-attributes. |
| — | — | `Planet.region_id` | UUID FK | **Compute**: orchestrator-supplied. |

#### Citadel sub-fields (when `citadel != null`)

| bang field | gameserver column | notes |
|---|---|---|
| `citadel.level` | `Planet.citadel_level` | Direct (0..5). |
| `citadel.droneCapacity` | `Planet.citadel_drone_capacity` | Direct. |
| `citadel.safeContents` | `Planet.citadel_safe_credits` | Direct (starts 0 per bang). |
| `citadel.droneInventory` | (no direct column) | **Drop / compute**: gameserver has no per-planet "uninstalled drone inventory" column; goes to 0. Either drop or stash on `Planet.economy` JSONB. |
| — | `Planet.citadel_safe_max` | **Compute**: from `CitadelTier.safeStorage` for the matching level. |
| — | `Planet.citadel_max_population` | **Compute**: from CITADEL_LEVELS table (gameserver-side). |

When `citadel = null`, all `citadel_*` columns default to 0.

### 2.7 Nebula

There is no `nebulae` table on the gameserver. A bang `Nebula` collapses onto its host Sector.

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `type` | `NebulaType` (`normal`\|`magnetic`) | `Sector.special_features += [f"nebula_type:{type}"]` | ARRAY(String) | No enum table; tag onto `special_features`. |
| `density` | number | `Sector.special_features += [f"nebula_density:{n}"]` OR `Sector.nav_hazards["nebula_density"]` | ARRAY(String) / JSONB | Stash in `nav_hazards` JSONB for runtime lookups; keep a marker in `special_features` for filtering. |
| — | — | `Sector.type` | Enum `sector_type` | **Compute**: when nebula present, flip from `STANDARD` to `NEBULA`. |

**Open question Q5**: should we add a dedicated `nebulae` table for proper enum/density tracking? Bang's walking-skeleton currently emits empty arrays, so this is deferrable.

### 2.8 SpecialLocation

5 canonical slugs: `terra`, `stardock`, `rylan`, `alpha_centauri`, `fringe_homeworld`. Decorative — they stamp existing Sector rows; they do NOT create new rows.

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `type` | `SpecialLocationType` | `Sector.special_features += [f"special_location:{type}"]` | ARRAY(String) | Tag on host sector. |
| `sectorId` | number | (used to look up the Sector to decorate) | — | Resolve via int→UUID map. |

Side-effects:
- `Sector.name` is overridden from the default `"Sector {n}"` to the canonical name (e.g., `"Terra"`, `"Stardock"`).
- For `type=stardock`, the host sector typically also has a `Port` with `isSpaceDock=true`; the Port→Station mapping handles that independently.
- `terra` host sector should additionally flip `Sector.security_level=10` and `Sector.controlling_faction='terran_federation'`.

**Drop**: bang's `specialLocations[]` array itself is not persisted — it's pure decoration.

### 2.9 SpecialFormation

| bang field | bang type | gameserver column | gameserver type | mapping notes |
|---|---|---|---|---|
| `id` | number | (translator UUID; recorded in `formation int→UUID` map for `BACKDOOR.target_formation_id` resolution) | — | Bang's int id NOT persisted. |
| `type` | `SpecialFormationType` (12 values v1.3.0) | `SpecialFormation.type` | Enum `special_formation_type` (9 values) | **Mismatch**: gameserver enum is missing the 3 ADR-0070 island types (`LOST_SECTOR`, `LOST_CLUSTER`, `ARCHIPELAGO`). Open question Q6 — needs Alembic migration to add these enum values. |
| `name` | string | (no dedicated column) | — | **Drop or compute**: gameserver `SpecialFormation` has no `name` column. Could be stashed in `properties.name` JSONB. Open question Q7. |
| `anchorSectorId` | number | `SpecialFormation.anchor_sector_id` | UUID FK (ondelete=RESTRICT) | Resolve via int→UUID map. |
| `interiorSectorIds` | `number[]` | `SpecialFormation.interior_sector_ids` | ARRAY(UUID) | Map each int → UUID. Empty array for single-sector formations. |
| `properties.*` | `FormationProperties` | `SpecialFormation.properties` | JSONB | Direct copy. All keys (interior_size, gateway_count, branching, length, one_way_bias, entry_count, recovery_method, parent_kind, is_unfigged_only, target_formation_id, entry_distance, bypass_distance, surprise_source_distance, exit_warp_sector_id, island_member_cluster_ids, quantum_jump_distance) flow into the JSONB column. The `target_formation_id` int needs to remain an int (it references bang's formation-id, which the gameserver translator can resolve later if needed) OR be rewritten to a UUID via the formation map — **open question Q8**. |
| `clusterId` | number | (DROP — bang-internal nav aid) | — | Per bang's own docstring (`types.ts:592-594`): "Bang-internal navigation aids... gameserver-side schema does NOT carry these; the translator drops them." |
| `endpointClusterId` | number? | (DROP — same reason) | — | Same. |
| `isDiscovered` | boolean | `SpecialFormation.is_discovered` | BOOLEAN | Direct. |
| `isHidden` | boolean | (no dedicated column) | — | **Drop or stash**: gameserver has `discovery_requirement` JSONB (nullable) and `is_discovered` boolean, but no `is_hidden`. Per the integration plan, `is_hidden=true` should likely populate `discovery_requirement` with a non-null sentinel. Open question Q9. |
| — | — | `SpecialFormation.region_id` | UUID FK | **Compute**: orchestrator-supplied. |
| — | — | `SpecialFormation.generation_seed` | VARCHAR nullable | **Compute**: copy `Galaxy.bang_seed` or derive a per-formation seed. |

### 2.10 NPCRoster

There is currently **no `npc_rosters` table** on the gameserver. Per ADR-0069 + the bang docstring, these are placeholders that `npc_scheduler.bootstrap_region()` later materializes into `NPCCharacter` rows. Neither `NPCCharacter` nor `NPCBarracks`/`OutlawBase` exists on the gameserver yet.

**Two viable strategies**:
- **Strategy A (deferred materialization)**: Translator stores `npcRosters[]` array verbatim on `Galaxy.bang_snapshot.npc_rosters`. The `npc_scheduler` reads from there at runtime.
- **Strategy B (staging table)**: Add a new `npc_rosters` table in the same Alembic migration; translator inserts rows; scheduler queries by region_id.

Strategy A is lower-risk for the initial cut. **Open question Q3**.

| bang field | bang type | gameserver column (Strategy B) | gameserver type | mapping notes |
|---|---|---|---|---|
| `id` | number | `NPCRoster.id` (UUID; map int→UUID) | UUID | Bang's int id NOT persisted as PK. |
| `kind` | `NPCRosterKind` (7 values) | `NPCRoster.kind` | Enum or VARCHAR | Values: `federation_marshal`, `marshal_captain`, `nexus_sentinel`, `sentinel_captain`, `pirate_lord`, `pirate_captain`, `pirate_enforcer`. |
| `factionCode` | string (slug) | `NPCRoster.faction_code` | VARCHAR | Resolves to `factions.name` lookup. |
| `targetCount` | number | `NPCRoster.target_count` | INTEGER | Direct. |
| `hostSectorId` | number | `NPCRoster.host_sector_id` | UUID FK | Resolve via map. |
| `namePool` | string[] | `NPCRoster.name_pool` | ARRAY(String) or JSONB | Direct copy. Scheduler pulls from this pool when materializing characters. |
| `defaultLodgingId` | number \| null | `NPCRoster.default_lodging_id` | UUID FK nullable | Bang emits null in walking-skeleton-plus (no OutlawBase/NPCBarracks). |

---

## 3. Drop list (bang emits, gameserver discards)

| bang location | reason for drop |
|---|---|
| `Sector.warps[]` (per-sector adjacency list) | Redundant with top-level `Universe.warps[]` edge list; translator uses the canonical list. |
| `SpecialFormation.clusterId` | Bang's docstring (`types.ts:592-594`) explicitly says: bang-internal nav aid, gameserver schema does not carry this. |
| `SpecialFormation.endpointClusterId` | Same. |
| `SpecialFormation.name` | No corresponding column on `SpecialFormation`. Could be stashed in `properties.name` (TBD — Q7). |
| `SpecialFormation.isHidden` | No corresponding column; partially representable via `discovery_requirement` (TBD — Q9). |
| `Citadel.droneInventory` | No corresponding column on `Planet`; bang seeds to 0 anyway, so no information loss. |
| `Universe.specialLocations[]` (array itself) | Used to decorate Sector rows; not persisted as its own table. |
| `Cluster.id` (int) | Bang's int is translator-scope only; gameserver uses UUID PK. |
| `Sector.id` (int as Universe key) | Translator builds `int → UUID` map; bang's int continues to live as `Sector.sector_id` (INTEGER unique). |
| `Warp` from→to int IDs after resolution | Same — translator resolves to UUIDs. |
| `Universe.fedspaceSectors[]` (array itself) | Drives Sector flag updates; not persisted as its own table/column. |
| `BigBangConfig.density / portPercent / planetPercent / nebulaPercent / stardock` | Walking-skeleton emits these but bang documents them as "parsed-but-deferred". Live verbatim inside `Galaxy.bang_snapshot.config` for audit; not separately persisted. |

---

## 4. Compute list (gameserver needs, bang doesn't emit)

These columns require translator-side computation, runtime materialization, or operator entry.

### Translator computes during import

- `Galaxy.id`, `Galaxy.name`, `Galaxy.description`, `Galaxy.expansion_enabled`, `Galaxy.resources_regenerate`, `Galaxy.warp_shifts_enabled`, `Galaxy.default_turns_per_day`, `Galaxy.faction_influence`, `Galaxy.state`, `Galaxy.events`, `Galaxy.statistics`, `Galaxy.density`, `Galaxy.combat_penalties`, `Galaxy.economic_modifiers` — gameserver defaults or admin-form input; bang doesn't model galaxy-level config.
- `Cluster.region_id` — orchestrator-supplied.
- `Cluster.controlling_faction`, `Cluster.faction_influence`, `Cluster.resource_modifiers`, `Cluster.economic_focus`, `Cluster.resources`, `Cluster.special_features`, `Cluster.description`, `Cluster.nav_hazards`, `Cluster.discovery_requirement` — gameserver defaults; faction_influence per ADR-0069 §50 stays gameserver-side.
- `Sector.name` (default `"Sector {n}"` unless Special Location overrides), `Sector.cluster_id`, `Sector.region_id`, `Sector.zone_id`, `Sector.type`, `Sector.security_level`, `Sector.development_level`, `Sector.traffic_level`, `Sector.hazard_level`, `Sector.radiation_level`, `Sector.resources`, `Sector.resource_regeneration`, `Sector.defenses`, `Sector.controlling_faction`, `Sector.special_features`, `Sector.description` — translator builds from cluster.type + special-location flags + gameserver defaults.
- `sector_warps.turn_cost`, `sector_warps.warp_stability` — translator copies from cluster.
- `Station.sector_id`, `Station.sector_uuid`, `Station.type`, `Station.status`, `Station.size`, `Station.faction_affiliation`, `Station.trade_volume`, `Station.market_volatility`, `Station.trader_personality`, `Station.services`, `Station.service_prices`, `Station.defenses`, `Station.ownership`, `Station.acquisition_requirements`, `Station.region_id`, `Station.description` — gameserver defaults or derived from cluster.type/class.
- `Station.commodities` — per-commodity dict assembled from bang's 9-commodity catalog + class trading pattern + commodity catalog (base_price/variance from `GenerationRequest.commodityCatalog`).
- `Planet.sector_id`, `Planet.sector_uuid`, `Planet.region_id`, `Planet.status`, `Planet.size`, `Planet.position`, `Planet.gravity`, `Planet.planet_type` (the string-typed shadow column), `Planet.specialization`, `Planet.atmosphere`, `Planet.temperature`, `Planet.water_coverage`, `Planet.radiation_level`, `Planet.resource_richness`, `Planet.resources`, `Planet.special_resources`, `Planet.fighters`, `Planet.population`, `Planet.population_growth`, `Planet.fuel_allocation`, `Planet.organics_allocation`, `Planet.equipment_allocation`, `Planet.economy`, `Planet.production`, `Planet.production_efficiency`, building levels, defense fields — gameserver defaults.
- `Planet.citadel_safe_max`, `Planet.citadel_max_population` — looked up from CITADEL_LEVELS tier table when citadel is present.
- `SpecialFormation.region_id`, `SpecialFormation.generation_seed`, `SpecialFormation.discovery_requirement` — translator computes.

### Runtime materializes after import

- `Galaxy.statistics` live counters update via existing `Galaxy.update_statistics()` machinery.
- `NPCCharacter` rows (when scheduler runs) — see §2.10.
- `Sector.players_present`, `Sector.ships_present`, `Sector.last_combat`, `Sector.active_events` — gameplay state, not generation state.

### Operator sets (admin UI / pre-import)

- `Region.name`, `Region.display_name`, `Region.region_type`, `Region.owner_id`, `Region.subscription_*`, `Region.governance_*`, `Region.tax_rate`, `Region.starting_credits`, `Region.starting_ship`, `Region.language_pack`, `Region.aesthetic_theme`, `Region.traditions`, `Region.social_hierarchy`, `Region.nexus_warp_gate_sector`, `Region.total_sectors` — operator-driven; the orchestrator passes a `region_id` to the translator.

---

## 5. Type coercion notes

| Source | Coercion | Notes |
|---|---|---|
| Bang sector `id: number` (1..N int) | → Python `int` → SQLAlchemy `Integer` | Used for `Sector.sector_id`/`Sector.sector_number`. The gameserver UUID PK `Sector.id` is freshly minted via `uuid.uuid4()`. |
| Bang cross-references by int `sectorId` | → resolved to `uuid.UUID` via in-memory `Dict[int, uuid.UUID]` | Translator must build the sector map BEFORE writing warps, ports, planets, formations, rosters. |
| Bang `seed: number` | → Python `int` → SQLAlchemy `BigInteger` | Bang docs say "positive int"; emit as JS number which fits in int53. Store as BIGINT to be safe. |
| Bang `createdAt: string` (ISO-8601) | → `datetime.fromisoformat(s)` → SQLAlchemy `DateTime(timezone=True)` | Bang emits with timezone (`...Z` or `+00:00`); Python `fromisoformat` from 3.11+ handles both. |
| Bang `ClusterType` string enum | → Python `cluster_type` SQLEnum | Values match byte-for-byte; pass through `ClusterType[bang_value]`. |
| Bang `PlanetType` (6 values) | → gameserver `PlanetType` (12 values) via lookup dict | See §2.6. Bang `earth → TERRAN`, `glacial → ICE`, `barren → BARREN`, etc. |
| Bang `SpecialFormationType` (12 values v1.3.0) | → gameserver enum (currently 9 values) | **MIGRATION REQUIRED** to add LOST_SECTOR, LOST_CLUSTER, ARCHIPELAGO. See Q6. |
| Bang `Warp.oneWay: bool` | → `sector_warps.is_bidirectional: bool` | **Inverted** — easy translator bug. |
| Bang `Port.class: number` | → `Station.station_class: StationClass(int)` | Direct via `StationClass(int_val)`. |
| Bang `PortCommodityState.action: 'B'|'S'` | → `{buys: bool, sells: bool}` | `'B' → buys=True, sells=False` and vice versa. Bang doesn't emit both. |
| Bang `PortCommodityState.regenRate` | → `production_rate` (renamed) | Direct value transfer with column rename. |
| Bang `Planet.ore` | → `Planet.fuel_ore` (renamed) | Watch for this rename. |
| Bang `interiorSectorIds: number[]` | → `ARRAY(UUID)` via per-element map lookup | Postgres `ARRAY(UUID)` requires `uuid.UUID` instances. |
| Bang `FormationProperties.target_formation_id: number` | → JSONB int OR resolved-to-UUID in JSONB | Unclear which; see Q8. |
| Bang `namePool: string[]` | → `ARRAY(String)` or JSONB array | Either works; gameserver-side schema TBD when Strategy A vs B is chosen (Q3). |
| Bang `Citadel.safeContents`, `maxPopulation` | → SQLAlchemy `BigInteger` | Bang emits numbers up to `habitabilityScore * 1000` ≤ 100,000 today; future-proof as BIGINT (already is). |
| Whole Universe JSON | → `Galaxy.bang_snapshot: JSONB` | Stored verbatim via `json.dumps(universe)` → asyncpg JSONB encoder. |

---

## 6. Resolved Decisions + Remaining Questions

**Updated 2026-05-31.** Q1–Q4 + Q6 resolved through documentation + direct code inspection. No Max input required for the items below; see `bang-integration.md` § "Resolved schema decisions" for the locked-in answers reflected here.

### Q1 — RESOLVED → 9th commodity is `precious_metals`
ADR-0062 E-D1 (`sw2102-docs/ADR/0062-group-f-economy-tariffs-contracts.md:45,57`) explicitly closes this gap: existing `COMMODITY_PRICE_RANGES` carries 8 (`ore, organics, gourmet_food, fuel, equipment, exotic_technology, luxury_goods, colonists`); E-D1 adds **`precious_metals`** as the 9th (canonical mining drop per `FEATURES/economy/mining.md`, target band 80-180 cr/unit). bang already emits 9-commodity per E-D2 (confirmed in `bang/src/content.ts:178`: Class 3 sells `['ore', 'organics', 'fuel', 'precious_metals', 'gourmet_food']`). Gameserver's `Station.commodities` default + `COMMODITY_PRICE_RANGES` are **behind spec**.
**Action**: Phase 1 Alembic migration extends `Station.commodities` default to include `precious_metals` and adds it to `COMMODITY_PRICE_RANGES`.

### Q2 — RESOLVED → add `Station.is_spacedock` column
Confirmed: no `is_spacedock` column exists anywhere in gameserver models (grep returned 0 hits).
**Action**: Phase 1 Alembic migration adds `Station.is_spacedock BOOLEAN NOT NULL DEFAULT false`.

### Q3 — RESOLVED → Strategy A (snapshot stash, defer relational persistence)
Per `sw2102-docs/SYSTEMS/galaxy-generator-design.md:279-310`, the canonical design is for NPCRoster/NPCCharacter/NPCBarracks/OutlawBase to be relational entities with an `npc_scheduler.bootstrap_region` hook. Direct code inspection confirms **none of these models exist on gameserver today** and there is no scheduler. Building all four models + the scheduler is a multi-week initiative that would explode this integration's scope. Strategy A preserves bang's roster data losslessly in `Galaxy.bang_snapshot.npc_rosters` JSONB; when NPC infrastructure ships separately, the backfill reads from the snapshot.
**Action**: Translator writes bang's `NPCRoster[]` into `Galaxy.bang_snapshot.npc_rosters` (already-planned JSONB column). No new relational tables in Phase 1.
**Follow-up ticket** (out of scope): "Implement NPC relational infrastructure (NPCRoster/NPCCharacter/NPCBarracks/OutlawBase + scheduler)."

### Q4 — RESOLVED → schema map was wrong; direct UUID mapping
Direct read of `models/planet.py:59` shows `owner_id = Column(UUID(as_uuid=True), nullable=True)`. The earlier audit's "string vs UUID" framing was incorrect. Bang emits `ownerId` as UUID → direct mapping, no coercion required.
**Action**: Translator maps `bang.Planet.ownerId` → `Planet.owner_id` directly. No schema change.

### Q5 (MEDIUM) — Nebula representation
No dedicated `nebulae` table. Currently bang emits empty arrays in walking-skeleton, so this is deferrable. But once nebulae are stamped, do we want a real table or is the Sector-decoration approach (`special_features` + `nav_hazards`) sufficient? **Decide before nebulae generation lands in bang.**

### Q6 — RESOLVED → add 3 enum values via Alembic
Confirmed by direct read of `models/special_formation.py:10-19`: gameserver enum has 9 values (`BUBBLE, DEAD_END_BUBBLE, GOLD_BUBBLE, TUNNEL, DEAD_END, WARP_SINK, BACKDOOR, BLISTER, ESCAPE_HATCH`); bang v1.3.0 emits 12 (the 9 plus ADR-0070's `LOST_SECTOR`, `LOST_CLUSTER`, `ARCHIPELAGO`).
**Action**: Phase 1 Alembic migration extends the Postgres enum with the 3 missing values.

### Q7 (MEDIUM) — `SpecialFormation.name` has no column
Bang emits an AI-generated `name`; gameserver `SpecialFormation` model has no `name` column. Options: (a) stash in `properties.name` JSONB; (b) add a `name VARCHAR(100)` column. Option (a) is zero-migration; (b) makes UI display easier.

### Q8 (MEDIUM) — `target_formation_id` in `FormationProperties`
`BACKDOOR` formations reference another formation by bang's int id. The translator could either (a) leave it as an int in the JSONB (gameserver resolves at query time via a side map), or (b) rewrite to the UUID at translate time. Option (b) is cleaner; (a) is faster to implement. **Decide.**

### Q9 (LOW) — `SpecialFormation.isHidden` semantics
Gameserver has `is_discovered` and `discovery_requirement` but no `is_hidden`. Likely `isHidden=true` should populate `discovery_requirement` with a non-null sentinel like `{"requires_probe": true}`. Confirm UI semantics with Max.

### Q10 (LOW) — Cluster `sectorRangeStart/End` durable storage
Currently mapped to `Cluster.stats` JSONB. If the translator needs to repeatedly query "which cluster contains sector N?", a top-level pair of columns + index would be faster than JSONB lookups. Low priority — the translator builds the inverse map in memory during import.

### Q11 (LOW) — Zone derivation
Bang has no concept of `Zone` (which the gameserver uses for Federation/Border/Frontier policing tiers). Translator default: split sectors into thirds by sector_number. Is this acceptable, or does Max want zone boundaries derived from cluster.type instead?

---

*End of schema map. Hand off to Translator Author with this document + the integration plan as context. Q1–Q4 + Q6 are resolved and locked into Phase 1's Alembic migration scope. Q5 + Q7–Q11 remain MEDIUM/LOW open questions; Translator Author may pick defaults (each open question lists its own).*
