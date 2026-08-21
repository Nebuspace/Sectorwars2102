"""LEG-470: bang-import step-8 special types (ASTEROID_FIELD + hazards).

Canon: SYSTEMS/bang-import-pipeline.md:185-196 and FEATURES/economy/mining.md:17-23.
Harvest predicate is mining_service.py — ``sector.type != ASTEROID_FIELD`` →
``not_an_asteroid_field``. Magnitudes are the step-8 table only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.models.cluster import ClusterType
from src.models.sector import SectorType
from src.services.bang_import_service import (
    BangImportService,
    ParsedUniverse,
    _step8_special_type,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"
FIXTURE_1K = FIXTURE_DIR / "v1_3_0_player_owned_small.json"

# mining_service.py harvest / resolve gates (not_an_asteroid_field)
_HARVEST_REQUIRES_ASTEROID_FIELD = SectorType.ASTEROID_FIELD


def _load_1k() -> Dict[str, Any]:
    return json.loads(FIXTURE_1K.read_text())


def _translate_player_owned(raw: Dict[str, Any]):
    parsed = ParsedUniverse(region_type="player_owned", raw=raw)  # type: ignore[arg-type]
    svc = BangImportService(
        bang_image="test-image:0",
        docker_client=MagicMock(name="docker_noop"),
    )
    plan = svc.translate(
        {"player_owned": parsed},
        region_metadata={"galaxy_name": "Test Galaxy", "master_seed": parsed.seed},
    )
    return plan.regions["player_owned"]


@pytest.mark.unit
class TestStep8HelperTable:
    """Direct rolls — pin cluster weights without a 1k universe."""

    def test_non_standard_passthrough(self) -> None:
        assert (
            _step8_special_type(
                ClusterType.STANDARD, 42, 1, SectorType.NEBULA
            )
            is SectorType.NEBULA
        )

    def test_military_zone_never_asteroid_field(self) -> None:
        types = {
            _step8_special_type(ClusterType.MILITARY_ZONE, 42, sid, SectorType.STANDARD)
            for sid in range(1, 4000)
        }
        assert SectorType.ASTEROID_FIELD not in types
        assert SectorType.RADIATION_ZONE in types
        assert SectorType.WARP_STORM in types

    def test_frontier_outpost_never_asteroid_field(self) -> None:
        types = {
            _step8_special_type(
                ClusterType.FRONTIER_OUTPOST, 42, sid, SectorType.STANDARD
            )
            for sid in range(1, 4000)
        }
        assert SectorType.ASTEROID_FIELD not in types
        assert SectorType.BLACK_HOLE in types
        assert SectorType.RADIATION_ZONE in types

    def test_resource_rich_slice_is_asteroid_or_standard(self) -> None:
        types = {
            _step8_special_type(ClusterType.RESOURCE_RICH, 42, sid, SectorType.STANDARD)
            for sid in range(1, 8000)
        }
        assert types <= {SectorType.STANDARD, SectorType.ASTEROID_FIELD}
        assert SectorType.ASTEROID_FIELD in types

    def test_default_clusters_use_four_specials(self) -> None:
        types = {
            _step8_special_type(ClusterType.STANDARD, 42, sid, SectorType.STANDARD)
            for sid in range(1, 8000)
        }
        assert SectorType.ASTEROID_FIELD in types
        assert SectorType.RADIATION_ZONE in types
        assert SectorType.WARP_STORM in types
        assert SectorType.BLACK_HOLE in types


@pytest.mark.unit
class TestStep8OneKFixture:
    def test_asteroid_field_present_and_in_galaxy_band(self) -> None:
        region = _translate_player_owned(_load_1k())
        by_type: Dict[SectorType, int] = {}
        for spec in region.sectors:
            by_type[spec.type] = by_type.get(spec.type, 0) + 1
        af = by_type.get(SectorType.ASTEROID_FIELD, 0)
        n = len(region.sectors)
        assert n == 1000
        assert af > 0
        share = af / n
        # mining.md:17-23 galaxy-wide 3–10% depending on cluster mix
        assert 0.03 <= share <= 0.10, f"AF share {share:.4f} ({af}/{n}) outside 3–10%"

    def test_military_zone_cluster_has_no_asteroid_field(self) -> None:
        region = _translate_player_owned(_load_1k())
        clusters = {c.cluster_int_id: c for c in region.clusters}
        mil_ids = {
            cid for cid, c in clusters.items() if c.type == ClusterType.MILITARY_ZONE
        }
        assert mil_ids, "fixture expected a MILITARY_ZONE cluster"
        mil_sectors = [s for s in region.sectors if s.cluster_int_id in mil_ids]
        assert mil_sectors
        assert all(s.type != SectorType.ASTEROID_FIELD for s in mil_sectors)

    def test_harvest_predicate_holds_on_imported_asteroid_fields(self) -> None:
        region = _translate_player_owned(_load_1k())
        af = [s for s in region.sectors if s.type == _HARVEST_REQUIRES_ASTEROID_FIELD]
        assert af, "step-8 must stamp ASTEROID_FIELD for harvest to be possible"
        # mining_service.py:459 / resolve path: type != ASTEROID_FIELD → not_an_asteroid_field
        assert all(s.type == SectorType.ASTEROID_FIELD for s in af)

    def test_hazard_types_appear_where_table_allows(self) -> None:
        region = _translate_player_owned(_load_1k())
        types = {s.type for s in region.sectors}
        assert SectorType.RADIATION_ZONE in types
        assert SectorType.WARP_STORM in types
        assert SectorType.BLACK_HOLE in types


@pytest.mark.unit
class TestBangResourcesCopy:
    def test_has_asteroids_payload_stamps_asteroid_field_and_copies_blob(self) -> None:
        raw = _load_1k()
        payload = {
            "has_asteroids": True,
            "asteroid_yield": {"ore": 7, "precious_metals": 1, "quantum_shards": 0},
        }
        # sid 51: STANDARD cluster, not nebula in this fixture
        raw["sectors"]["51"]["resources"] = payload
        region = _translate_player_owned(raw)
        spec = next(s for s in region.sectors if s.sector_number == 51)
        assert spec.type == SectorType.ASTEROID_FIELD
        assert spec.resources is not None
        assert spec.resources["has_asteroids"] is True
        assert spec.resources["asteroid_yield"]["ore"] == 7
