"""Phase 4B translator coverage: per-fixture invariants.

These tests supplement (do not replace) ``test_bang_import_service.py``.
They walk all three captured fixtures and assert the schema-map
contracts (Q1/Q2/Q3/Q6, ADR-0070 no cross-island warps) on the real
v1.3.0 payload, plus the starter invariants for terran_space.

The terran_space starter invariants run as proper assertions because the
translator post-processes the bang plan (``_apply_terran_space_invariants``)
to enforce them. If a future bang release ships them itself the
post-processor can be relaxed without breaking these tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from src.models.cluster import ClusterType
from src.models.special_formation import SpecialFormationType
from src.models.station import StationClass, StationType
from src.services.bang_import_service import (
    COMMODITY_WIRE_ORDER,
    BangImportService,
    InsertPlan,
    ParsedUniverse,
    _coerce_formation_type,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"
FIXTURES: Dict[str, Tuple[str, str]] = {
    "player_owned": ("player_owned", "v1_3_0_player_owned_small.json"),
    "terran_space": ("terran_space", "v1_3_0_terran_space.json"),
    "central_nexus": ("central_nexus", "v1_3_0_central_nexus.json"),
}


def _load(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())  # type: ignore[no-any-return]


def _parsed(region_type: str, name: str) -> ParsedUniverse:
    return ParsedUniverse(region_type=region_type, raw=_load(name))  # type: ignore[arg-type]


@pytest.fixture
def service() -> BangImportService:
    return BangImportService(bang_image="test-image:0")


@pytest.fixture(params=list(FIXTURES.values()), ids=list(FIXTURES.keys()))
def parsed_fixture(request: pytest.FixtureRequest) -> ParsedUniverse:
    region_type, filename = request.param
    return _parsed(region_type, filename)


def _translate_single(svc: BangImportService, parsed: ParsedUniverse) -> InsertPlan:
    return svc.translate(
        {parsed.region_type: parsed},
        region_metadata={"galaxy_name": "Test Galaxy", "master_seed": parsed.seed},
    )


# ---------------------------------------------------------------------------
# Region count + structural invariants (per-fixture parametrised)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegionCounts:
    """Every fixture round-trips into a plan that preserves universe counts."""

    def test_sector_count_matches_universe(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        assert len(region.sectors) == parsed_fixture.raw["totalSectors"]
        assert region.total_sectors == parsed_fixture.raw["totalSectors"]

    def test_cluster_count_matches_universe(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        assert len(region.clusters) == len(parsed_fixture.raw["clusters"])
        assert {c.cluster_int_id for c in region.clusters} == {
            int(c["id"]) for c in parsed_fixture.raw["clusters"]
        }

    def test_warp_count_matches_universe(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        assert len(region.warps) == len(parsed_fixture.raw.get("warps") or [])

    def test_formation_count_matches_universe(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        assert len(region.formations) == len(
            parsed_fixture.raw.get("specialFormations") or []
        )


# ---------------------------------------------------------------------------
# Q1 — 9-commodity wire across every station in every fixture
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQ1CommodityWire:
    """Every station in every fixture exposes all 9 commodities incl. precious_metals."""

    def test_all_stations_have_nine_keys(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        if not region.stations:
            pytest.skip(f"{parsed_fixture.region_type} fixture has no stations")
        expected_keys = set(COMMODITY_WIRE_ORDER)
        for station in region.stations:
            assert set(station.commodities.keys()) == expected_keys
            assert "precious_metals" in station.commodities
            assert station.commodities["precious_metals"]["base_price"] > 0


# ---------------------------------------------------------------------------
# Q2 — Station.is_spacedock mirrors Port.isSpaceDock
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQ2SpaceDock:
    """``Port.isSpaceDock=true`` produces ``Station.is_spacedock=True``."""

    def test_isspacedock_ports_become_is_spacedock(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]

        # Collect bang-side ports (excluding any sector-1/sector-10 override
        # from the terran_space invariants enforcer).
        bang_spacedock_sectors: set[int] = set()
        for sid_str, sector_payload in parsed_fixture.raw["sectors"].items():
            port = sector_payload.get("port")
            if port and port.get("isSpaceDock"):
                bang_spacedock_sectors.add(int(sid_str))
        if not bang_spacedock_sectors:
            pytest.skip(f"{parsed_fixture.region_type} fixture has no spacedocks")

        station_by_sector = {s.sector_int_id: s for s in region.stations}
        for sid in bang_spacedock_sectors:
            station = station_by_sector.get(sid)
            assert station is not None, f"missing station for spacedock sector {sid}"
            assert station.is_spacedock is True
            # Spacedock stations route to SHIPYARD via _build_station_spec,
            # except for the terran_space sector-10 override which also
            # produces SHIPYARD + CLASS_11.
            assert station.station_type == StationType.SHIPYARD
            assert station.services["genesis_dealer"] is True
            assert station.services["drone_shop"] is True

    def test_non_spacedock_ports_stay_false(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        bang_non_dock_sectors: set[int] = set()
        for sid_str, sector_payload in parsed_fixture.raw["sectors"].items():
            port = sector_payload.get("port")
            if port and not port.get("isSpaceDock"):
                bang_non_dock_sectors.add(int(sid_str))
        for station in region.stations:
            if station.sector_int_id in bang_non_dock_sectors:
                # Sector 1 may be replaced by the terran_space invariants
                # enforcer with Earth Station (non-spacedock) — still false.
                assert station.is_spacedock is False


# ---------------------------------------------------------------------------
# Q3 — NPCRosters live in Galaxy.bang_snapshot.regions.<rt>.universe.npcRosters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQ3NpcRosterStash:
    """Strategy A: NPC rosters stored verbatim inside the per-region snapshot blob."""

    def test_rosters_in_snapshot_universe_blob(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        snapshot_region = plan.bang_snapshot["regions"][parsed_fixture.region_type]
        assert (
            snapshot_region["universe"]["npcRosters"]
            == parsed_fixture.raw["npcRosters"]
        )

    def test_no_relational_roster_rows_on_plan(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        # raw_npc_rosters is a list mirror, not an ORM spec list.
        assert isinstance(region.raw_npc_rosters, list)
        assert not hasattr(region, "rosters")


# ---------------------------------------------------------------------------
# Q6 — Bang's 3 island-formation enum values round-trip through translate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQ6FormationEnum:
    """LOST_SECTOR / LOST_CLUSTER / ARCHIPELAGO survive translate without error."""

    @pytest.mark.parametrize(
        "formation_type", ["LOST_SECTOR", "LOST_CLUSTER", "ARCHIPELAGO"]
    )
    def test_island_formation_roundtrip(
        self, service: BangImportService, formation_type: str
    ) -> None:
        universe = self._mk_universe_with_formation(formation_type)
        parsed = ParsedUniverse(region_type="player_owned", raw=universe)
        plan = service.translate(
            {"player_owned": parsed},
            region_metadata={"galaxy_name": "T"},
        )
        formations = plan.regions["player_owned"].formations
        assert len(formations) == 1
        assert formations[0].type == formation_type
        # The Postgres-enum widening helper should accept the value.
        coerced = _coerce_formation_type(formation_type)
        assert isinstance(coerced, SpecialFormationType)

    def test_player_owned_fixture_carries_island_formations(
        self, service: BangImportService
    ) -> None:
        parsed = _parsed("player_owned", "v1_3_0_player_owned_small.json")
        plan = _translate_single(service, parsed)
        types_seen = {f.type for f in plan.regions["player_owned"].formations}
        # Per fixture inspection: this small fixture has LOST_SECTOR /
        # LOST_CLUSTER / ARCHIPELAGO present. If a future capture changes
        # this, drop the asserts here rather than failing.
        assert "LOST_SECTOR" in types_seen
        assert "LOST_CLUSTER" in types_seen
        assert "ARCHIPELAGO" in types_seen

    @staticmethod
    def _mk_universe_with_formation(formation_type: str) -> Dict[str, Any]:
        return {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 1,
            "sectors": {
                "1": {
                    "id": 1,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "warps": [],
                    "port": None,
                    "planets": [],
                    "navHazards": [],
                    "nebula": None,
                    "beacon": None,
                    "explored": True,
                }
            },
            "warps": [],
            "specialLocations": [],
            "fedspaceSectors": [],
            "config": {},
            "createdAt": "2026-05-31T00:00:00Z",
            "clusters": [
                {
                    "id": 1,
                    "name": "Test",
                    "type": "STANDARD",
                    "sectorRangeStart": 1,
                    "sectorRangeEnd": 1,
                    "sectorCount": 1,
                    "coords": {"x": 0, "y": 0, "z": 0},
                    "warpStability": 1.0,
                    "economicValue": 50,
                    "recommendedShipClass": "any",
                    "maxWarps": 6,
                    "isDiscovered": True,
                    "isHidden": False,
                }
            ],
            "specialFormations": [
                {
                    "id": 1,
                    "type": formation_type,
                    "name": f"Test {formation_type}",
                    "anchorSectorId": 1,
                    "interiorSectorIds": [],
                    "properties": {"distance": 1},
                    "clusterId": 1,
                    "isDiscovered": False,
                    "isHidden": True,
                }
            ],
            "npcRosters": [],
        }


# ---------------------------------------------------------------------------
# ADR-0070 — cross-island warps must not be emitted
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestADR0070CrossIslandWarps:
    """Per ADR-0070: bang never emits a warp crossing two islandGroupIds."""

    def test_no_cross_island_warps(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        cluster_by_sector: Dict[int, Any] = {}
        for c in region.clusters:
            for sid in range(c.sector_range_start, c.sector_range_end + 1):
                cluster_by_sector[sid] = c
        cross = 0
        for w in region.warps:
            fc = cluster_by_sector.get(w.from_sector_int)
            tc = cluster_by_sector.get(w.to_sector_int)
            if fc is None or tc is None:
                continue
            fi = fc.island_group_id
            ti = tc.island_group_id
            if fi is not None and ti is not None and fi != ti:
                cross += 1
        assert cross == 0, (
            f"{parsed_fixture.region_type}: {cross} cross-island warps "
            "found in plan (ADR-0070 violation)"
        )


# ---------------------------------------------------------------------------
# Cluster mapping — types coerce to gameserver ClusterType enum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClusterTypeCoercion:
    """Every bang cluster.type string maps cleanly to a gameserver enum member."""

    def test_all_cluster_types_in_enum(
        self, service: BangImportService, parsed_fixture: ParsedUniverse
    ) -> None:
        plan = _translate_single(service, parsed_fixture)
        region = plan.regions[parsed_fixture.region_type]
        for c in region.clusters:
            assert isinstance(c.type, ClusterType)


# ---------------------------------------------------------------------------
# terran_space starter invariants (locked by _apply_terran_space_invariants)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerranSpaceStarterInvariants:
    """Sector 1 safe, Earth Station + New Earth, Sector 10 Stardock CLASS_11."""

    @pytest.fixture
    def plan(self, service: BangImportService) -> InsertPlan:
        parsed = _parsed("terran_space", "v1_3_0_terran_space.json")
        return _translate_single(service, parsed)

    def test_sector_one_safe(self, plan: InsertPlan) -> None:
        sector_1 = next(
            s for s in plan.regions["terran_space"].sectors if s.sector_id == 1
        )
        assert sector_1.hazard_level == 0
        assert sector_1.security_level == 10
        assert "fedspace" in sector_1.special_features

    def test_earth_station_present(self, plan: InsertPlan) -> None:
        s1 = [
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 1
        ]
        assert len(s1) == 1
        assert s1[0].name == "Earth Station"
        assert s1[0].is_spacedock is False

    def test_earth_station_is_class_zero_capital(self, plan: InsertPlan) -> None:
        # ADR-0005: the Sol capital is CLASS_0 and (per the class pattern)
        # sells colonists so first-login players can begin colonizing.
        earth = next(
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 1
        )
        assert earth.station_class == StationClass.CLASS_0
        assert earth.station_type == StationType.TRADING
        colonists = earth.commodities["colonists"]
        assert colonists["sells"] is True
        assert colonists["quantity"] >= 1

    def test_earth_station_orientation_stock(self, plan: InsertPlan) -> None:
        # Galaxy-generation step 8: the capital carries standard commodities
        # in low quantities for orientation trades.
        earth = next(
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 1
        )
        for key in ("ore", "organics", "fuel"):
            commodity = earth.commodities[key]
            assert commodity["sells"] is True, f"capital should sell {key}"
            assert 1 <= commodity["quantity"] <= 1000, (
                f"capital {key} stock should be modest, got {commodity['quantity']}"
            )

    def test_new_earth_planet_present(self, plan: InsertPlan) -> None:
        p1 = [
            p for p in plan.regions["terran_space"].planets if p.sector_int_id == 1
        ]
        assert len(p1) == 1
        assert p1[0].name == "New Earth"
        assert p1[0].max_population == 8_000_000_000

    def test_sector_ten_stardock_class_eleven(self, plan: InsertPlan) -> None:
        s10 = [
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 10
        ]
        assert len(s10) == 1
        sdock = s10[0]
        assert sdock.is_spacedock is True
        assert sdock.station_class == StationClass.CLASS_11
        assert sdock.station_type == StationType.SHIPYARD
        for flag in (
            "ship_dealer",
            "ship_upgrades",
            "drone_shop",
            "genesis_dealer",
            "mine_dealer",
            "insurance",
        ):
            assert sdock.services[flag] is True

    def test_stardock_not_fully_inert(self, plan: InsertPlan) -> None:
        # The injected Stardock spec goes through the same class-pattern
        # finalization as bang stations; CLASS_11 buys exotic_technology,
        # so at least one commodity must be actively traded.
        sdock = next(
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 10
        )
        active = [
            k for k, c in sdock.commodities.items() if c["buys"] or c["sells"]
        ]
        assert active, "Stardock commodities are fully inert"
        assert sdock.commodities["exotic_technology"]["buys"] is True

    def test_all_terran_stations_actively_trade(self, plan: InsertPlan) -> None:
        # Class-pattern finalization guarantees no station imports inert:
        # every class pattern references at least one real wire commodity.
        for station in plan.regions["terran_space"].stations:
            active = [
                k for k, c in station.commodities.items() if c["buys"] or c["sells"]
            ]
            assert active, (
                f"station {station.name} (sector {station.sector_int_id}, "
                f"{station.station_class.name}) is fully inert"
            )


# ---------------------------------------------------------------------------
# Multi-region translate — three regions in one InsertPlan
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiRegionTranslate:
    """Translate() accepts all 3 regions at once and produces a coherent plan."""

    def test_three_region_plan_round_trip(
        self, service: BangImportService
    ) -> None:
        universes = {
            "player_owned": _parsed(
                "player_owned", "v1_3_0_player_owned_small.json"
            ),
            "terran_space": _parsed("terran_space", "v1_3_0_terran_space.json"),
            "central_nexus": _parsed(
                "central_nexus", "v1_3_0_central_nexus.json"
            ),
        }
        plan = service.translate(
            universes,  # type: ignore[arg-type]
            region_metadata={"galaxy_name": "Three Region", "master_seed": 42},
        )
        assert set(plan.regions.keys()) == {
            "player_owned",
            "terran_space",
            "central_nexus",
        }
        assert plan.bang_seed == 42
        assert plan.galaxy_name == "Three Region"
        # bang_snapshot carries the per-region universe blob.
        for rt in ("player_owned", "terran_space", "central_nexus"):
            assert "universe" in plan.bang_snapshot["regions"][rt]

    def test_inconsistent_versions_raise(
        self, service: BangImportService
    ) -> None:
        u1 = _parsed("player_owned", "v1_3_0_player_owned_small.json")
        # Mutate a copy to a different version
        raw = dict(u1.raw)
        raw["version"] = "1.2.9"
        u2 = ParsedUniverse(region_type="terran_space", raw=raw)
        with pytest.raises(ValueError, match="Inconsistent bang versions"):
            service.translate(
                {"player_owned": u1, "terran_space": u2},
                region_metadata={"galaxy_name": "X"},
            )

    def test_empty_universes_raises(
        self, service: BangImportService
    ) -> None:
        with pytest.raises(ValueError, match="at least one Universe"):
            service.translate({}, region_metadata={"galaxy_name": "X"})


# ---------------------------------------------------------------------------
# Derived sector names — slugs from specialLocations map to canonical names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpecialLocationNaming:
    """``_derive_sector_name`` maps known slugs to canonical names."""

    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("terra", "Terra"),
            ("stardock", "Stardock"),
            ("rylan", "Rylan"),
            ("alpha_centauri", "Alpha Centauri"),
            ("fringe_homeworld", "Fringe Homeworld"),
        ],
    )
    def test_known_slugs_map_to_canonical_names(
        self, slug: str, expected: str
    ) -> None:
        out = BangImportService._derive_sector_name(  # type: ignore[arg-type]
            42, {42: slug}
        )
        assert out == expected

    def test_unknown_slug_falls_back_to_sector_id(self) -> None:
        out = BangImportService._derive_sector_name(  # type: ignore[arg-type]
            99, {99: "unknown_slug"}
        )
        assert out == "Sector 99"

    def test_no_slug_uses_sector_id(self) -> None:
        out = BangImportService._derive_sector_name(7, {})  # type: ignore[arg-type]
        assert out == "Sector 7"


# ---------------------------------------------------------------------------
# Unknown formation types raise via _coerce_formation_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoerceFormationTypeFailure:
    """Bang must not invent a formation type the migration hasn't added."""

    def test_unknown_formation_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown SpecialFormationType"):
            _coerce_formation_type("NOT_A_REAL_FORMATION_TYPE")


# ---------------------------------------------------------------------------
# terran_space starter invariant warning fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerranSpaceInvariantsWarningPath:
    """If the bang JSON has no Sector 1, the enforcer records a warning."""

    def test_missing_sector_one_records_warning(
        self, service: BangImportService
    ) -> None:
        # Build a minimal terran_space-shaped universe with totalSectors=300
        # (validator wants 300) but with sector 1 deliberately absent.
        sectors_dict: Dict[str, Dict[str, Any]] = {
            str(i): {
                "id": i,
                "position": {"x": 0, "y": 0, "z": 0},
                "warps": [],
                "port": None,
                "planets": [],
                "navHazards": [],
                "nebula": None,
                "beacon": None,
                "explored": True,
            }
            for i in range(2, 302)
        }
        universe = {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 300,
            "sectors": sectors_dict,
            "warps": [],
            "specialLocations": [],
            "fedspaceSectors": [],
            "config": {},
            "createdAt": "2026-05-31T00:00:00Z",
            "clusters": [
                {
                    "id": 1,
                    "name": "Solo",
                    "type": "STANDARD",
                    "sectorRangeStart": 2,
                    "sectorRangeEnd": 301,
                    "sectorCount": 300,
                    "coords": {"x": 0, "y": 0, "z": 0},
                    "warpStability": 1.0,
                    "economicValue": 50,
                    "recommendedShipClass": "any",
                    "maxWarps": 6,
                    "isDiscovered": True,
                    "isHidden": False,
                }
            ],
            "specialFormations": [],
            "npcRosters": [],
        }
        parsed = ParsedUniverse(region_type="terran_space", raw=universe)
        plan = service.translate(
            {"terran_space": parsed},
            region_metadata={"galaxy_name": "Missing S1"},
        )
        # One STARTER_INVARIANT warning is emitted.
        codes = [w["code"] for w in plan.generation_warnings]
        assert "INV-001" in codes


# ---------------------------------------------------------------------------
# Sector→cluster mismatch raises
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSectorClusterCoverage:
    """A sector outside every cluster.sectorRange raises a hard error."""

    def test_sector_outside_cluster_range_raises(
        self, service: BangImportService
    ) -> None:
        universe = {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 1,
            "sectors": {
                "999": {
                    "id": 999,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "warps": [],
                    "port": None,
                    "planets": [],
                    "navHazards": [],
                    "nebula": None,
                    "beacon": None,
                    "explored": True,
                }
            },
            "warps": [],
            "specialLocations": [],
            "fedspaceSectors": [],
            "config": {},
            "createdAt": "2026-05-31T00:00:00Z",
            "clusters": [
                {
                    "id": 1,
                    "name": "Test",
                    "type": "STANDARD",
                    "sectorRangeStart": 1,
                    "sectorRangeEnd": 1,
                    "sectorCount": 1,
                    "coords": {"x": 0, "y": 0, "z": 0},
                    "warpStability": 1.0,
                    "economicValue": 50,
                    "recommendedShipClass": "any",
                    "maxWarps": 6,
                    "isDiscovered": True,
                    "isHidden": False,
                }
            ],
            "specialFormations": [],
            "npcRosters": [],
        }
        parsed = ParsedUniverse(region_type="player_owned", raw=universe)
        with pytest.raises(ValueError, match="not covered by any cluster"):
            service.translate(
                {"player_owned": parsed}, region_metadata={"galaxy_name": "Bad"}
            )

    def test_universe_without_clusters_raises(
        self, service: BangImportService
    ) -> None:
        universe = {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 0,
            "sectors": {},
            "warps": [],
            "clusters": [],
            "specialLocations": [],
            "fedspaceSectors": [],
            "config": {},
            "createdAt": "2026-05-31T00:00:00Z",
            "specialFormations": [],
            "npcRosters": [],
        }
        parsed = ParsedUniverse(region_type="player_owned", raw=universe)
        with pytest.raises(ValueError, match="missing required `clusters"):
            service.translate(
                {"player_owned": parsed}, region_metadata={"galaxy_name": "Bad"}
            )


# ---------------------------------------------------------------------------
# config_hash stability — does not include orchestrator metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigHashIndependence:
    """Galaxy name / region_id metadata must not affect the config hash."""

    def test_metadata_does_not_alter_hash(
        self, service: BangImportService
    ) -> None:
        parsed = _parsed("player_owned", "v1_3_0_player_owned_small.json")
        plan_a = service.translate(
            {"player_owned": parsed},
            region_metadata={"galaxy_name": "Alpha", "master_seed": 1},
        )
        plan_b = service.translate(
            {"player_owned": parsed},
            region_metadata={
                "galaxy_name": "Beta",
                "master_seed": 999,
                "regions": {"player_owned": {"region_id": "00000000-0000-0000-0000-000000000001"}},
            },
        )
        assert plan_a.bang_config_hash == plan_b.bang_config_hash
