"""Unit tests for :class:`BangImportService`.

These tests target the **pure** surface of the translator:
:meth:`translate`, :meth:`validate_only`, :meth:`invoke_bang`. They use the
captured bang fixtures under ``tests/fixtures/bang/`` and mock the
subprocess boundary; no DB session is constructed.

The DB-writing :meth:`apply` is type-checked but not exercised here — Phase 4
will add an integration suite once a DB harness is in place.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ReadTimeout  # noqa: F401 - used by test helpers below

from src.schemas.bang_config import BangConfig
from src.services.bang_import_service import (
    COMMODITY_WIRE_ORDER,
    BangImportService,
    ParsedUniverse,
    _build_full_commodities,
    _validate_universe_shape,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"
FIXTURE_PLAYER_OWNED = FIXTURE_DIR / "v1_3_0_player_owned_small.json"
FIXTURE_TERRAN_SPACE = FIXTURE_DIR / "v1_3_0_terran_space.json"
FIXTURE_CENTRAL_NEXUS = FIXTURE_DIR / "v1_3_0_central_nexus.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _parsed(region_type: str, path: Path) -> ParsedUniverse:
    return ParsedUniverse(region_type=region_type, raw=_load_fixture(path))  # type: ignore[arg-type]


@pytest.fixture
def parsed_player_owned() -> ParsedUniverse:
    return _parsed("player_owned", FIXTURE_PLAYER_OWNED)


@pytest.fixture
def parsed_terran_space() -> ParsedUniverse:
    return _parsed("terran_space", FIXTURE_TERRAN_SPACE)


@pytest.fixture
def parsed_central_nexus() -> ParsedUniverse:
    return _parsed("central_nexus", FIXTURE_CENTRAL_NEXUS)


def _fake_docker_client(stdout: str = "", stderr: str = "", exit_code: int = 0) -> MagicMock:
    """Mock the docker-py chain used by ``BangImportService.invoke_bang`` /
    ``validate_only`` after the docker-ce-cli → docker-py CVE refactor.

    Identical pattern to ``_fake_docker`` in ``test_bang_invoke_mock.py``;
    kept inline here to avoid cross-file test imports.
    """
    container = MagicMock(name="container")
    container.wait.return_value = {"StatusCode": exit_code}

    stdout_bytes = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
    stderr_bytes = stderr.encode("utf-8") if isinstance(stderr, str) else stderr

    def _logs(**kw: Any) -> bytes:
        if kw.get("stdout") and not kw.get("stderr"):
            return stdout_bytes
        if kw.get("stderr") and not kw.get("stdout"):
            return stderr_bytes
        return b""

    container.logs.side_effect = _logs
    client = MagicMock(name="docker_client")
    client.containers.run.return_value = container
    return client


@pytest.fixture
def service() -> BangImportService:
    return BangImportService(bang_image="test-image:0", docker_client=MagicMock(name="docker_noop"))


# ---------------------------------------------------------------------------
# Fixture sanity — confirms the captured JSON is what we expect
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFixtureSanity:
    """Confirms our captured fixtures match the bang v1.3.0 contract."""

    def test_fixtures_exist(self) -> None:
        assert FIXTURE_PLAYER_OWNED.exists()
        assert FIXTURE_TERRAN_SPACE.exists()
        assert FIXTURE_CENTRAL_NEXUS.exists()

    def test_player_owned_shape(self, parsed_player_owned: ParsedUniverse) -> None:
        u = parsed_player_owned.raw
        assert u["version"].startswith("1.")
        assert u["totalSectors"] == 1000
        assert len(u["sectors"]) == 1000
        assert u.get("clusters") and len(u["clusters"]) > 0

    def test_terran_space_shape(self, parsed_terran_space: ParsedUniverse) -> None:
        u = parsed_terran_space.raw
        assert u["totalSectors"] == 300
        # Canonical 5 special locations
        slugs = {sl["type"] for sl in u["specialLocations"]}
        assert {"terra", "stardock", "rylan", "alpha_centauri", "fringe_homeworld"} <= slugs
        # Sector 1 hosts Terra
        terra = next(sl for sl in u["specialLocations"] if sl["type"] == "terra")
        assert terra["sectorId"] == 1

    def test_central_nexus_shape(self, parsed_central_nexus: ParsedUniverse) -> None:
        assert parsed_central_nexus.raw["totalSectors"] == 5000


# ---------------------------------------------------------------------------
# translate() — region-level invariants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTranslateRegionInvariants:
    """Per-region invariants produced by :meth:`BangImportService.translate`."""

    def test_player_owned_region_counts_match_universe(
        self, service: BangImportService, parsed_player_owned: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"player_owned": parsed_player_owned},
            region_metadata={"galaxy_name": "Test Galaxy", "master_seed": 42},
        )
        region = plan.regions["player_owned"]
        u = parsed_player_owned.raw
        assert len(region.sectors) == u["totalSectors"]
        assert len(region.clusters) == len(u["clusters"])
        assert len(region.warps) == len(u["warps"])
        # Formations + rosters should round-trip in shape
        assert len(region.formations) == len(u.get("specialFormations") or [])
        assert len(region.raw_npc_rosters) == len(u.get("npcRosters") or [])

    def test_warps_invert_oneway_to_is_bidirectional(
        self, service: BangImportService, parsed_player_owned: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"player_owned": parsed_player_owned},
            region_metadata={"galaxy_name": "Test"},
        )
        region = plan.regions["player_owned"]
        for w_spec, w_raw in zip(region.warps, parsed_player_owned.raw["warps"]):
            assert w_spec.is_bidirectional is (not bool(w_raw.get("oneWay")))

    def test_translate_preserves_fedspace(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "Test"},
        )
        region = plan.regions["terran_space"]
        assert sorted(region.fedspace_sector_ints) == sorted(
            parsed_terran_space.raw["fedspaceSectors"]
        )
        # Every fedspace sector ends up with security_level=10 and the
        # "fedspace" tag in its special_features.
        fedspace_set = set(region.fedspace_sector_ints)
        for sector in region.sectors:
            if sector.sector_id in fedspace_set:
                assert sector.security_level == 10
                assert "fedspace" in sector.special_features


# ---------------------------------------------------------------------------
# Q1 — 9-commodity wire including precious_metals
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommodityWire:
    """Every Station.commodities dict carries all 9 keys per ADR-0062 E-D1."""

    def test_default_dict_has_nine_keys(self) -> None:
        c = _build_full_commodities({})
        assert set(c.keys()) == set(COMMODITY_WIRE_ORDER)
        assert "precious_metals" in c

    def test_default_dict_preserves_default_prices(self) -> None:
        c = _build_full_commodities({})
        assert c["precious_metals"]["base_price"] == 130

    def test_translated_stations_have_nine_keys(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        region = plan.regions["terran_space"]
        assert region.stations, "expected at least one station in terran_space"
        for station in region.stations:
            assert set(station.commodities.keys()) == set(COMMODITY_WIRE_ORDER)
            # precious_metals is always present
            assert "precious_metals" in station.commodities

    def test_action_b_maps_to_buys_true(self) -> None:
        c = _build_full_commodities(
            {"ore": {"action": "B", "quantity": 10, "capacity": 100, "regenRate": 5}}
        )
        assert c["ore"]["buys"] is True
        assert c["ore"]["sells"] is False
        assert c["ore"]["quantity"] == 10
        assert c["ore"]["capacity"] == 100
        assert c["ore"]["production_rate"] == 5

    def test_action_s_maps_to_sells_true(self) -> None:
        c = _build_full_commodities(
            {"organics": {"action": "S", "quantity": 1, "capacity": 2, "regenRate": 3}}
        )
        assert c["organics"]["sells"] is True
        assert c["organics"]["buys"] is False


# ---------------------------------------------------------------------------
# Q2 — Station.is_spacedock for bang's isSpaceDock ports
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpaceDockFlag:
    """A bang ``Port.isSpaceDock=true`` ends up on ``Station.is_spacedock=True``."""

    def test_stardock_sector_becomes_spacedock(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        region = plan.regions["terran_space"]
        # Sector 10 hosts Stardock per bang's terran_space recipe.
        stardock_stations = [s for s in region.stations if s.sector_int_id == 10]
        assert stardock_stations, "expected stardock station in sector 10"
        assert all(s.is_spacedock for s in stardock_stations)
        # Service flags should include genesis_dealer + ship_dealer for SpaceDock.
        sdock = stardock_stations[0]
        assert sdock.services["genesis_dealer"] is True
        assert sdock.services["ship_dealer"] is True
        assert sdock.services["drone_shop"] is True
        assert sdock.services["mine_dealer"] is True


# ---------------------------------------------------------------------------
# Q3 — NPC rosters stashed in Galaxy.bang_snapshot (as part of the full
# Universe blob per the Phase 1B handoff)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNpcRosters:
    """NPCRosters live inside Universe.npcRosters, which lives inside bang_snapshot."""

    def test_rosters_round_trip_via_snapshot(
        self,
        service: BangImportService,
        parsed_terran_space: ParsedUniverse,
        parsed_player_owned: ParsedUniverse,
    ) -> None:
        plan = service.translate(
            {
                "terran_space": parsed_terran_space,
                "player_owned": parsed_player_owned,
            },
            region_metadata={"galaxy_name": "T"},
        )
        # The full Universe blob is stored per region.
        for region_type, parsed in (
            ("terran_space", parsed_terran_space),
            ("player_owned", parsed_player_owned),
        ):
            snapshot_region = plan.bang_snapshot["regions"][region_type]
            assert snapshot_region["universe"]["npcRosters"] == parsed.raw["npcRosters"]

    def test_no_relational_roster_rows_emitted(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        # The per-region plan exposes raw_npc_rosters as a list — confirm
        # they came through but are NOT modeled as ORM specs.
        region = plan.regions["terran_space"]
        assert isinstance(region.raw_npc_rosters, list)
        assert len(region.raw_npc_rosters) > 0
        # The InsertPlan has no `rosters` field on any spec — this is the
        # Strategy A contract.
        assert not hasattr(region, "rosters")


# ---------------------------------------------------------------------------
# Q4 — Planet.owner_id is the bang UUID directly
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPlanetOwnerDirectMap:
    """Bang emits ``Planet.ownerId`` as UUID; translator preserves it as-is."""

    def test_uuid_owner_mapped_to_owner_id(
        self, service: BangImportService
    ) -> None:
        from uuid import uuid4
        owner = uuid4()
        bang_planet = {
            "name": "Test",
            "type": "earth",
            "owner": str(owner),
            "habitabilityScore": 80,
            "maxPopulation": 80000,
            "maxColonists": 1000,
            "ore": 0,
            "organics": 0,
            "equipment": 0,
            "colonists": 0,
            "citadel": None,
        }
        spec = service._build_planet_spec(  # pylint: disable=protected-access
            sector_id=42, p=bang_planet
        )
        assert spec.owner_id == owner

    def test_non_uuid_owner_becomes_none(self, service: BangImportService) -> None:
        bang_planet = {
            "name": "Terra",
            "type": "earth",
            "owner": "terran_federation",  # bang sometimes emits faction slugs
            "habitabilityScore": 100,
            "maxPopulation": 100000,
            "maxColonists": 1000,
            "ore": 0,
            "organics": 0,
            "equipment": 0,
            "colonists": 0,
            "citadel": None,
        }
        spec = service._build_planet_spec(  # pylint: disable=protected-access
            sector_id=1, p=bang_planet
        )
        assert spec.owner_id is None


# ---------------------------------------------------------------------------
# Q6 — Postgres enum extension passes through 3 island-formation types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpecialFormationEnumPassthrough:
    """The translator preserves bang's 12 v1.3.0 formation types untouched."""

    def test_translate_passes_lost_sector(self, service: BangImportService) -> None:
        # Synthesise a Universe with a LOST_SECTOR formation
        sector = {
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
        cluster = {
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
        universe = {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 1,
            "sectors": {"1": sector},
            "warps": [],
            "specialLocations": [],
            "fedspaceSectors": [],
            "config": {},
            "createdAt": "2026-05-31T00:00:00Z",
            "clusters": [cluster],
            "specialFormations": [
                {
                    "id": 1,
                    "type": "LOST_SECTOR",
                    "name": "Forgotten Hold",
                    "anchorSectorId": 1,
                    "interiorSectorIds": [],
                    "properties": {"quantum_jump_distance": 7},
                    "clusterId": 1,
                    "isDiscovered": False,
                    "isHidden": True,
                }
            ],
            "npcRosters": [],
        }
        parsed = ParsedUniverse(region_type="player_owned", raw=universe)
        plan = service.translate(
            {"player_owned": parsed},
            region_metadata={"galaxy_name": "T"},
        )
        formations = plan.regions["player_owned"].formations
        assert len(formations) == 1
        assert formations[0].type == "LOST_SECTOR"
        # clusterId is dropped from properties per the schema map drop list
        assert "clusterId" not in formations[0].properties
        # Anchor must reference bang's int id; it gets resolved to UUID in apply()
        assert formations[0].anchor_sector_int == 1


# ---------------------------------------------------------------------------
# terran_space starter invariants (per legacy GalaxyGenerator audit)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTerranSpaceStarterInvariants:
    """Sector 1 safe, Earth Station, New Earth (8B pop), Sector-10 SpaceDock."""

    def test_sector_one_is_safe(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        sector_1 = next(s for s in plan.regions["terran_space"].sectors if s.sector_id == 1)
        assert sector_1.hazard_level == 0
        assert sector_1.security_level == 10
        assert "fedspace" in sector_1.special_features

    def test_earth_station_in_sector_one(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        s1_stations = [
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 1
        ]
        assert len(s1_stations) == 1
        assert s1_stations[0].name == "Earth Station"
        assert s1_stations[0].is_spacedock is False

    def test_new_earth_planet(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        s1_planets = [
            p for p in plan.regions["terran_space"].planets if p.sector_int_id == 1
        ]
        assert len(s1_planets) == 1
        assert s1_planets[0].name == "New Earth"
        assert s1_planets[0].max_population == 8_000_000_000

    def test_sector_ten_spacedock_class_eleven(
        self, service: BangImportService, parsed_terran_space: ParsedUniverse
    ) -> None:
        plan = service.translate(
            {"terran_space": parsed_terran_space},
            region_metadata={"galaxy_name": "T"},
        )
        s10_stations = [
            s for s in plan.regions["terran_space"].stations if s.sector_int_id == 10
        ]
        assert len(s10_stations) == 1
        sdock = s10_stations[0]
        assert sdock.is_spacedock is True
        from src.models.station import StationClass
        assert sdock.station_class == StationClass.CLASS_11
        # Legacy SpaceDock service flags
        for flag in ("ship_dealer", "ship_upgrades", "drone_shop", "genesis_dealer", "mine_dealer", "insurance"):
            assert sdock.services[flag] is True


# ---------------------------------------------------------------------------
# invoke_bang() — subprocess mocking
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangSubprocess:
    """:meth:`invoke_bang` parses stdout, raises on bad exit / bad JSON."""

    def test_happy_path_parses_universe(
        self, parsed_terran_space: ParsedUniverse
    ) -> None:
        stdout_payload = json.dumps(parsed_terran_space.raw)
        client = _fake_docker_client(stdout=stdout_payload, exit_code=0)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=300, region_type="terran_space")
        result = svc.invoke_bang(config)
        assert result.region_type == "terran_space"
        assert result.total_sectors == 300

    def test_nonzero_exit_raises(self) -> None:
        client = _fake_docker_client(stdout="", stderr="boom", exit_code=1)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=300, region_type="terran_space")
        with pytest.raises(RuntimeError, match="bang exited 1"):
            svc.invoke_bang(config)

    def test_invalid_json_raises(self) -> None:
        client = _fake_docker_client(stdout="not json", exit_code=0)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=300, region_type="terran_space")
        with pytest.raises(RuntimeError, match="invalid JSON"):
            svc.invoke_bang(config)

    def test_unexpected_sector_count_raises(self) -> None:
        bad_universe = {
            "version": "1.3.0",
            "seed": 1,
            "totalSectors": 999,  # terran_space must be 300
            "sectors": {},
            "warps": [],
        }
        client = _fake_docker_client(stdout=json.dumps(bad_universe), exit_code=0)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=300, region_type="terran_space")
        with pytest.raises(ValueError, match="expected 300 sectors"):
            svc.invoke_bang(config)


@pytest.mark.unit
class TestValidateOnly:
    """:meth:`validate_only` returns stats/warnings without a Universe body."""

    def test_returns_report(self) -> None:
        stdout_payload = json.dumps(
            {
                "stats": {"sectors": 1000, "clusters": 20},
                "warnings": [{"code": "B-100", "message": "preview only"}],
                "validation": {"passed": True},
            }
        )
        client = _fake_docker_client(stdout=stdout_payload, exit_code=0)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=1000, region_type="player_owned")
        report = svc.validate_only(config)
        assert report.stats["sectors"] == 1000
        assert report.warnings[0]["code"] == "B-100"
        assert report.validation["passed"] is True

    def test_exit_code_two_accepted(self) -> None:
        client = _fake_docker_client(stdout="{}", stderr="some warnings", exit_code=2)

        svc = BangImportService(bang_image="test", docker_client=client)
        config = BangConfig(seed=42, sectors=1000, region_type="player_owned")
        # exit code 2 is "validation warnings" — should NOT raise.
        report = svc.validate_only(config)
        assert report.stats == {}


# ---------------------------------------------------------------------------
# CLI arg construction — snake_case → kebab-case
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCliFlagMapping:
    """Optional :class:`BangConfig` fields map to kebab-case CLI flags."""

    def test_required_flags_always_present(self, service: BangImportService) -> None:
        config = BangConfig(seed=42, sectors=1000, region_type="player_owned")
        args = service._build_bang_args(config)  # pylint: disable=protected-access
        assert "--seed" in args
        assert "42" in args
        assert "--sectors" in args
        assert "1000" in args
        assert "--region-type" in args
        assert "player_owned" in args
        assert "--json-out" in args

    def test_validate_only_replaces_json_out(self, service: BangImportService) -> None:
        config = BangConfig(seed=42, sectors=1000, region_type="player_owned")
        args = service._build_bang_args(config, validate_only=True)  # pylint: disable=protected-access
        assert "--validate-only" in args
        assert "--json-out" not in args

    def test_optional_max_warps_becomes_kebab(self, service: BangImportService) -> None:
        config = BangConfig(
            seed=42, sectors=1000, region_type="player_owned", max_warps=8
        )
        args = service._build_bang_args(config)  # pylint: disable=protected-access
        assert "--max-warps" in args
        idx = args.index("--max-warps")
        assert args[idx + 1] == "8"

    def test_optional_one_way_percent_emitted(self, service: BangImportService) -> None:
        config = BangConfig(
            seed=42,
            sectors=1000,
            region_type="player_owned",
            one_way_warp_percent=12.5,
        )
        args = service._build_bang_args(config)  # pylint: disable=protected-access
        assert "--one-way-warps" in args

    def test_unset_optionals_not_emitted(self, service: BangImportService) -> None:
        config = BangConfig(seed=42, sectors=1000, region_type="player_owned")
        args = service._build_bang_args(config)  # pylint: disable=protected-access
        assert "--max-warps" not in args
        assert "--port-percent" not in args
        assert "--planet-percent" not in args


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUniverseShapeValidation:
    def test_missing_required_keys_raise(self) -> None:
        with pytest.raises(ValueError, match="missing required keys"):
            _validate_universe_shape({"version": "1.3.0"}, region_type="player_owned")

    def test_wrong_version_raises(self) -> None:
        payload = {
            "version": "2.0.0",
            "seed": 1,
            "totalSectors": 1,
            "sectors": {},
            "warps": [],
        }
        with pytest.raises(ValueError, match="not in supported 1.x line"):
            _validate_universe_shape(payload, region_type="player_owned")


# ---------------------------------------------------------------------------
# bang_config_hash — canonical JSON stability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigHashStability:
    """Hash is sha-256 over sorted-keys, no-whitespace JSON."""

    def test_hash_is_deterministic(
        self,
        service: BangImportService,
        parsed_player_owned: ParsedUniverse,
    ) -> None:
        plan1 = service.translate(
            {"player_owned": parsed_player_owned},
            region_metadata={"galaxy_name": "A"},
        )
        plan2 = service.translate(
            {"player_owned": parsed_player_owned},
            region_metadata={"galaxy_name": "B"},  # name doesn't affect hash
        )
        assert plan1.bang_config_hash == plan2.bang_config_hash
        assert len(plan1.bang_config_hash) == 64  # sha256 hex
