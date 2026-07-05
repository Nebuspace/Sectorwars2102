"""WO-SB-QH2 Lane A — density-derived canon nebula colors at bang import.

Covers the pure ``_derive_nebula_color`` boundary map, the canonical
``_NEBULA_COLOR_HEX`` table, and the ``_finalize_cluster_nebula_fields``
aggregation pass: every boundary edge, the no-samples → None case, and the
headline regression this WO fixes — a cluster's nebula_type now depends
ONLY on mean density, so a tie (or any split) among bang's raw
'normal'/'magnetic' per-sector sample counts can no longer affect the
outcome (the old Counter-based type majority vote is gone).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.models.cluster import ClusterType
from src.services.bang_import_service import (
    ClusterSpec,
    _derive_nebula_color,
    _finalize_cluster_nebula_fields,
    _NEBULA_COLOR_HEX,
)


def _spec(cluster_int_id: int = 1) -> ClusterSpec:
    """A minimally-populated ClusterSpec — only cluster_int_id and the three
    nebula fields matter to the code under test."""
    return ClusterSpec(
        cluster_int_id=cluster_int_id,
        name="Test Cluster",
        type=ClusterType.STANDARD,
        sector_range_start=1,
        sector_range_end=10,
        sector_count=10,
        x_coord=0,
        y_coord=0,
        z_coord=0,
        warp_stability=1.0,
        economic_value=0,
        recommended_ship_class="",
        max_warps=0,
        island_group_id=None,
        is_discovered=True,
        is_hidden=False,
        special_features=[],
    )


# ---------------------------------------------------------------------------
# _derive_nebula_color — pure boundary map
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeriveNebulaColorBoundaries:
    """quantum-resources.md nebula table cutpoints: ≥80 crimson, 60-79 azure,
    50-59 emerald, 40-49 violet, 20-39 amber, <20 obsidian."""

    @pytest.mark.parametrize(
        "density,expected",
        [
            (100, "crimson"),
            (80, "crimson"),  # crimson lower edge
            (79, "azure"),  # just below crimson
            (60, "azure"),  # azure lower edge
            (59, "emerald"),  # just below azure
            (50, "emerald"),  # emerald lower edge
            (49, "violet"),  # just below emerald
            (40, "violet"),  # violet lower edge
            (39, "amber"),  # just below violet
            (20, "amber"),  # amber lower edge
            (19, "obsidian"),  # just below amber
            (0, "obsidian"),
        ],
    )
    def test_boundary_edges(self, density: float, expected: str) -> None:
        assert _derive_nebula_color(density) == expected

    def test_fractional_mean_density_respects_boundary(self) -> None:
        # A mean of 79.9 (e.g. densities [79, 81] averaging 80.0 would tip
        # crimson; [79, 80] averages 79.5, still azure) stays on the correct
        # side of the crimson cutpoint.
        assert _derive_nebula_color(79.5) == "azure"
        assert _derive_nebula_color(80.0) == "crimson"


@pytest.mark.unit
class TestNebulaColorHexTable:
    """The canonical per-color hexes (quantum-resources.md:37-42) are canon,
    not a NO-CANON proposal — only the boundary cutpoints are."""

    def test_all_six_canon_colors_present(self) -> None:
        assert set(_NEBULA_COLOR_HEX.keys()) == {
            "crimson", "azure", "emerald", "violet", "amber", "obsidian",
        }

    @pytest.mark.parametrize(
        "color,hex_value",
        [
            ("crimson", "#DC143C"),
            ("azure", "#1E90FF"),
            ("emerald", "#00FF7F"),
            ("violet", "#9370DB"),
            ("amber", "#FF8C00"),
            ("obsidian", "#2F4F4F"),
        ],
    )
    def test_canonical_hex_values(self, color: str, hex_value: str) -> None:
        assert _NEBULA_COLOR_HEX[color] == hex_value

    def test_derived_color_always_has_a_hex(self) -> None:
        # Every possible _derive_nebula_color output must resolve in the hex
        # table — a KeyError here would mean the two tables drifted apart.
        for density in range(0, 101, 5):
            color = _derive_nebula_color(density)
            assert color in _NEBULA_COLOR_HEX


# ---------------------------------------------------------------------------
# _finalize_cluster_nebula_fields — the WO-DBB-QR4 aggregation pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinalizeClusterNebulaFields:
    def test_no_samples_leaves_all_three_fields_none(self) -> None:
        cs = _spec(cluster_int_id=1)
        _finalize_cluster_nebula_fields([cs], cluster_nebula_samples={})
        assert cs.nebula_type is None
        assert cs.quantum_field_strength is None
        assert cs.color_hex is None

    def test_empty_types_list_leaves_all_three_fields_none(self) -> None:
        cs = _spec(cluster_int_id=1)
        samples: Dict[int, Dict[str, List[Any]]] = {
            1: {"types": [], "densities": []},
        }
        _finalize_cluster_nebula_fields([cs], samples)
        assert cs.nebula_type is None
        assert cs.quantum_field_strength is None
        assert cs.color_hex is None

    def test_mean_density_drives_color_and_hex(self) -> None:
        cs = _spec(cluster_int_id=1)
        samples: Dict[int, Dict[str, List[Any]]] = {
            1: {"types": ["normal", "normal"], "densities": [85, 95]},  # mean 90
        }
        _finalize_cluster_nebula_fields([cs], samples)
        assert cs.quantum_field_strength == 90.0
        assert cs.nebula_type == "crimson"
        assert cs.color_hex == "#DC143C"

    def test_raw_type_tie_does_not_affect_outcome(self) -> None:
        """THE HEADLINE REGRESSION: before this WO, nebula_type was the most
        COMMON raw bang type (Counter.most_common, ties broken by
        first-seen). Two clusters with the SAME mean density but opposite
        raw-type majorities/orderings must now derive the IDENTICAL color —
        proving type no longer has any influence."""
        cs_normal_majority = _spec(cluster_int_id=1)
        cs_magnetic_majority = _spec(cluster_int_id=2)
        cs_exact_tie = _spec(cluster_int_id=3)
        samples: Dict[int, Dict[str, List[Any]]] = {
            1: {"types": ["normal", "normal", "magnetic"], "densities": [55, 55, 55]},
            2: {"types": ["magnetic", "magnetic", "normal"], "densities": [55, 55, 55]},
            3: {"types": ["normal", "magnetic"], "densities": [55, 55]},
        }
        _finalize_cluster_nebula_fields(
            [cs_normal_majority, cs_magnetic_majority, cs_exact_tie], samples
        )
        assert cs_normal_majority.nebula_type == "emerald"
        assert cs_magnetic_majority.nebula_type == "emerald"
        assert cs_exact_tie.nebula_type == "emerald"
        # And none of them carry bang's raw vocabulary through anymore.
        for cs in (cs_normal_majority, cs_magnetic_majority, cs_exact_tie):
            assert cs.nebula_type not in ("normal", "magnetic")

    def test_only_matching_cluster_int_id_is_finalized(self) -> None:
        cs_a = _spec(cluster_int_id=1)
        cs_b = _spec(cluster_int_id=2)
        samples: Dict[int, Dict[str, List[Any]]] = {
            1: {"types": ["normal"], "densities": [10]},
        }
        _finalize_cluster_nebula_fields([cs_a, cs_b], samples)
        assert cs_a.nebula_type == "obsidian"
        assert cs_b.nebula_type is None
        assert cs_b.quantum_field_strength is None
        assert cs_b.color_hex is None
