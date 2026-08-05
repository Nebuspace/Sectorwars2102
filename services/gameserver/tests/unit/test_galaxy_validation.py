"""ADR-0050 SK20: gameserver-canonical validation gate coverage.

A valid, real-fixture-translated plan passes with zero failures; plans
mutated to violate specific invariants are rejected with
``ERR_BANG_VALIDATION_FAILED`` and the itemized failing invariant(s).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import MagicMock

import pytest

from src.services.bang_import_service import BangImportService, InsertPlan, ParsedUniverse
from src.services.galaxy_validation import (
    ERR_BANG_VALIDATION_FAILED,
    GalaxyValidationError,
    validate_insert_plan,
    validate_insert_plan_or_raise,
    validate_region_plan,
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
    return BangImportService(bang_image="test-image:0", docker_client=MagicMock(name="docker_noop"))


@pytest.fixture
def real_plan(service: BangImportService) -> InsertPlan:
    """A plan translated from real captured bang fixtures (all 3 regions)."""
    universes = {rt: _parsed(rt, fname) for rt, (rt2, fname) in FIXTURES.items()}
    return service.translate(
        universes,  # type: ignore[arg-type]
        region_metadata={"galaxy_name": "SK20 Test Galaxy", "master_seed": 42},
    )


@pytest.mark.unit
class TestValidPlanPasses:
    def test_real_fixture_plan_has_zero_failures(self, real_plan: InsertPlan) -> None:
        failures = validate_insert_plan(real_plan)
        assert failures == [], [f.to_dict() for f in failures]

    def test_real_fixture_plan_does_not_raise(self, real_plan: InsertPlan) -> None:
        validate_insert_plan_or_raise(real_plan)  # must not raise


@pytest.mark.unit
class TestSectorCountMismatch:
    def test_declared_total_diverges_from_materialised_sectors(
        self, real_plan: InsertPlan
    ) -> None:
        region = real_plan.regions["player_owned"]
        mutated_region = dataclasses.replace(region, total_sectors=region.total_sectors + 5)
        real_plan.regions["player_owned"] = mutated_region

        failures = validate_insert_plan(real_plan)
        invariants = {f.invariant for f in failures}
        assert "sector_count" in invariants

        with pytest.raises(GalaxyValidationError) as excinfo:
            validate_insert_plan_or_raise(real_plan)
        assert ERR_BANG_VALIDATION_FAILED in str(excinfo.value)
        assert any(f.invariant == "sector_count" for f in excinfo.value.failures)


@pytest.mark.unit
class TestDuplicateSectorNumbers:
    def test_duplicate_sector_number_rejected(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["player_owned"]
        sectors = list(region.sectors)
        # Force a duplicate: second sector takes the first sector's number.
        sectors[1] = dataclasses.replace(sectors[1], sector_number=sectors[0].sector_number)
        real_plan.regions["player_owned"] = dataclasses.replace(region, sectors=sectors)

        failures = validate_insert_plan(real_plan)
        invariants = {f.invariant for f in failures}
        assert "sector_number_uniqueness" in invariants
        assert "sector_number_contiguity" in invariants  # the range now has a gap too


@pytest.mark.unit
class TestCapitalSectorInvariant:
    def test_no_capital_sector_rejected(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["terran_space"]
        sectors = [dataclasses.replace(s, is_capital=False) for s in region.sectors]
        real_plan.regions["terran_space"] = dataclasses.replace(region, sectors=sectors)

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "capital_sector_uniqueness" for f in failures)

    def test_two_capital_sectors_rejected(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["terran_space"]
        sectors = list(region.sectors)
        # Flag a second, non-capital sector as capital too.
        for idx, s in enumerate(sectors):
            if not s.is_capital:
                sectors[idx] = dataclasses.replace(s, is_capital=True)
                break
        real_plan.regions["terran_space"] = dataclasses.replace(region, sectors=sectors)

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "capital_sector_uniqueness" for f in failures)


@pytest.mark.unit
class TestWarpReferentialIntegrity:
    def test_dangling_warp_endpoint_rejected(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["player_owned"]
        warps = list(region.warps)
        bogus_sector_id = max(s.sector_id for s in region.sectors) + 10_000
        warps[0] = dataclasses.replace(warps[0], to_sector_int=bogus_sector_id)
        real_plan.regions["player_owned"] = dataclasses.replace(region, warps=warps)

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "warp_referential_integrity" for f in failures)


@pytest.mark.unit
class TestClusterReferentialIntegrity:
    def test_orphaned_cluster_reference_rejected(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["player_owned"]
        sectors = list(region.sectors)
        bogus_cluster_id = max(c.cluster_int_id for c in region.clusters) + 10_000
        sectors[0] = dataclasses.replace(sectors[0], cluster_int_id=bogus_cluster_id)
        real_plan.regions["player_owned"] = dataclasses.replace(region, sectors=sectors)

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "cluster_referential_integrity" for f in failures)


@pytest.mark.unit
class TestStationPlanetFormationReferentialIntegrity:
    def test_dangling_station_sector_rejected(self, real_plan: InsertPlan) -> None:
        region = next(r for r in real_plan.regions.values() if r.stations)
        stations = list(region.stations)
        bogus_sector_id = max(s.sector_id for s in region.sectors) + 10_000
        stations[0] = dataclasses.replace(stations[0], sector_int_id=bogus_sector_id)
        mutated = dataclasses.replace(region, stations=stations)
        real_plan.regions[region.region_type] = mutated

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "station_referential_integrity" for f in failures)

    def test_dangling_planet_sector_rejected(self, real_plan: InsertPlan) -> None:
        region = next(r for r in real_plan.regions.values() if r.planets)
        planets = list(region.planets)
        bogus_sector_id = max(s.sector_id for s in region.sectors) + 10_000
        planets[0] = dataclasses.replace(planets[0], sector_int_id=bogus_sector_id)
        mutated = dataclasses.replace(region, planets=planets)
        real_plan.regions[region.region_type] = mutated

        failures = validate_insert_plan(real_plan)
        assert any(f.invariant == "planet_referential_integrity" for f in failures)


@pytest.mark.unit
class TestMultipleSimultaneousViolations:
    """ADR-0050 SK20 requires the reject to list ALL failing invariants,
    not just the first one hit."""

    def test_three_distinct_invariant_types_all_reported(
        self, real_plan: InsertPlan
    ) -> None:
        region = real_plan.regions["player_owned"]

        # 1) sector_count mismatch
        region = dataclasses.replace(region, total_sectors=region.total_sectors + 1)

        # 2) dangling warp
        warps = list(region.warps)
        bogus_sector_id = max(s.sector_id for s in region.sectors) + 10_000
        warps[0] = dataclasses.replace(warps[0], to_sector_int=bogus_sector_id)
        region = dataclasses.replace(region, warps=warps)

        # 3) no capital sector
        sectors = [dataclasses.replace(s, is_capital=False) for s in region.sectors]
        region = dataclasses.replace(region, sectors=sectors)

        real_plan.regions["player_owned"] = region

        with pytest.raises(GalaxyValidationError) as excinfo:
            validate_insert_plan_or_raise(real_plan)

        invariants = {f.invariant for f in excinfo.value.failures}
        assert {"sector_count", "warp_referential_integrity", "capital_sector_uniqueness"} <= invariants
        assert ERR_BANG_VALIDATION_FAILED in excinfo.value.to_dict()["error"]


@pytest.mark.unit
class TestValidateRegionPlanHelper:
    """Single-region entry point (used by the add-region flow)."""

    def test_valid_region_plan_passes(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["player_owned"]
        assert validate_region_plan(region) == []

    def test_invalid_region_plan_fails(self, real_plan: InsertPlan) -> None:
        region = real_plan.regions["player_owned"]
        sectors = [dataclasses.replace(s, is_capital=False) for s in region.sectors]
        mutated = dataclasses.replace(region, sectors=sectors)
        failures = validate_region_plan(mutated)
        assert any(f.invariant == "capital_sector_uniqueness" for f in failures)
