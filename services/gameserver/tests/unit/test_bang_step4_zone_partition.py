"""LEG-471: bang-import step-4 Zone partition + Sector.zone_id.

Canon: SYSTEMS/bang-import-pipeline.md:126-136.
Policing/danger magnitudes are the table only — not invented.

Pirate ecosystem (pirate_ecosystem_service._zone_types_by_sector_id) maps a
missing Sector.zone_id to None. After Path A persist every imported sector
number must resolve to a ZoneType so that lookup is non-null.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import List, Set
from unittest.mock import MagicMock

import pytest

from src.models.zone import ZoneType
from src.services.bang_import_service import (
    BangImportService,
    ParsedUniverse,
    _step4_spec_for_sector,
    _step4_thirds_counts,
    _step4_zone_specs,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"


def _translate(region_type: str, filename: str):
    raw = __import__("json").loads((FIXTURE_DIR / filename).read_text())
    parsed = ParsedUniverse(region_type=region_type, raw=raw)  # type: ignore[arg-type]
    svc = BangImportService(
        bang_image="test-image:0",
        docker_client=MagicMock(name="docker_noop"),
    )
    plan = svc.translate(
        {region_type: parsed},
        region_metadata={"galaxy_name": "Test Galaxy", "master_seed": parsed.seed},
    )
    return plan.regions[region_type]


def _covered(specs, total: int) -> Set[int]:
    covered: Set[int] = set()
    for spec in specs:
        covered.update(range(spec.start_sector, spec.end_sector + 1))
    return covered


@pytest.mark.unit
class TestStep4HelperTable:
    def test_nexus_one_expanse_covers_full_width(self) -> None:
        specs = _step4_zone_specs("central_nexus", 5000)
        assert len(specs) == 1
        z = specs[0]
        assert z.zone_type is ZoneType.EXPANSE
        assert z.name == "The Expanse"
        assert z.start_sector == 1
        assert z.end_sector == 5000
        assert z.policing_level == 3
        assert z.danger_rating == 6
        assert _covered(specs, 5000) == set(range(1, 5001))

    def test_terran_thirds_300(self) -> None:
        # 300 * 33 // 100 = 99; remainder 102 → BORDER (34% of 300).
        assert _step4_thirds_counts(300) == (99, 102, 99)
        specs = _step4_zone_specs("terran_space", 300)
        assert [s.zone_type for s in specs] == [
            ZoneType.FEDERATION,
            ZoneType.BORDER,
            ZoneType.FRONTIER,
        ]
        assert specs[0].start_sector == 1 and specs[0].end_sector == 99
        assert specs[1].start_sector == 100 and specs[1].end_sector == 201
        assert specs[2].start_sector == 202 and specs[2].end_sector == 300
        assert (specs[0].policing_level, specs[0].danger_rating) == (9, 1)
        assert (specs[1].policing_level, specs[1].danger_rating) == (5, 4)
        assert (specs[2].policing_level, specs[2].danger_rating) == (2, 8)
        assert _covered(specs, 300) == set(range(1, 301))

    def test_player_owned_same_split_as_terran(self) -> None:
        a = _step4_zone_specs("player_owned", 1000)
        b = _step4_zone_specs("terran_space", 1000)
        assert [(s.zone_type, s.start_sector, s.end_sector) for s in a] == [
            (s.zone_type, s.start_sector, s.end_sector) for s in b
        ]
        # 1000 * 33 // 100 = 330; remainder 340 → BORDER.
        assert _step4_thirds_counts(1000) == (330, 340, 330)
        assert _covered(a, 1000) == set(range(1, 1001))

    def test_contiguous_no_overlap_no_gaps(self) -> None:
        for n in (3, 10, 100, 300, 1000):
            specs = _step4_zone_specs("player_owned", n)
            numbers: List[int] = []
            for spec in specs:
                assert spec.end_sector >= spec.start_sector
                numbers.extend(range(spec.start_sector, spec.end_sector + 1))
            assert numbers == list(range(1, n + 1))

    def test_small_n_keeps_valid_ranges(self) -> None:
        one = _step4_zone_specs("player_owned", 1)
        assert len(one) == 1 and one[0].zone_type is ZoneType.FEDERATION
        two = _step4_zone_specs("player_owned", 2)
        assert [s.zone_type for s in two] == [ZoneType.FEDERATION, ZoneType.FRONTIER]
        assert _covered(two, 2) == {1, 2}


@pytest.mark.unit
class TestPathAFixtureCoverage:
    def test_player_owned_1k_every_sector_maps(self) -> None:
        region = _translate("player_owned", "v1_3_0_player_owned_small.json")
        specs = _step4_zone_specs(region.region_type, region.total_sectors)
        types: Set[ZoneType] = set()
        for ss in region.sectors:
            spec = _step4_spec_for_sector(specs, ss.sector_number)
            assert spec.zone_type is not None
            types.add(spec.zone_type)
        assert types == {ZoneType.FEDERATION, ZoneType.BORDER, ZoneType.FRONTIER}
        assert len(region.sectors) == region.total_sectors

    def test_terran_300_every_sector_maps(self) -> None:
        region = _translate("terran_space", "v1_3_0_terran_space.json")
        specs = _step4_zone_specs(region.region_type, region.total_sectors)
        for ss in region.sectors:
            _step4_spec_for_sector(specs, ss.sector_number)

    def test_uncovered_sector_raises(self) -> None:
        specs = _step4_zone_specs("terran_space", 300)
        with pytest.raises(ValueError, match="does not cover"):
            _step4_spec_for_sector(specs, 0)
        with pytest.raises(ValueError, match="does not cover"):
            _step4_spec_for_sector(specs, 301)


@pytest.mark.unit
class TestPersistAndWipeWiring:
    def test_apply_region_constructs_zone_and_sets_zone_id(self) -> None:
        src = inspect.getsource(BangImportService._apply_region)
        assert "Zone(" in src
        assert 'sector_kwargs["zone_id"]' in src
        assert "_step4_zone_specs" in src

    def test_wipe_deletes_zones_after_sectors(self) -> None:
        src = inspect.getsource(BangImportService.wipe_region_content)
        assert "DELETE FROM zones WHERE region_id" in src
        sec = src.index("DELETE FROM sectors")
        zon = src.index("DELETE FROM zones")
        clu = src.index("DELETE FROM clusters")
        assert sec < zon < clu
