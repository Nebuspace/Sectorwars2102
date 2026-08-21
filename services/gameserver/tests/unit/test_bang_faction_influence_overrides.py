"""LEG-139: plumb RegionInsertPlan.faction_influence_overrides into §15 merge."""
from __future__ import annotations

from src.core.faction_profiles import (
    FACTION_KEYS,
    build_cluster_faction_influence,
    cluster_midpoint_sector,
    zone_type_for_sector,
)
from src.models.cluster import ClusterType
from src.services.bang_import_service import (
    ClusterSpec,
    RegionInsertPlan,
    _extract_faction_influence_overrides,
)


def _numeric_sum(influence: dict) -> int:
    return sum(int(influence[k]) for k in FACTION_KEYS)


def _minimal_cluster(
    *,
    cluster_int_id: int,
    range_start: int,
    range_end: int,
) -> ClusterSpec:
    return ClusterSpec(
        cluster_int_id=cluster_int_id,
        name=f"C{cluster_int_id}",
        type=ClusterType.STANDARD,
        sector_range_start=range_start,
        sector_range_end=range_end,
        sector_count=range_end - range_start + 1,
        x_coord=0,
        y_coord=0,
        z_coord=0,
        warp_stability=1.0,
        economic_value=0,
        recommended_ship_class="scout",
        max_warps=6,
        island_group_id=None,
        is_discovered=True,
        is_hidden=False,
        special_features=[],
    )


def _seed_cluster_influences(plan: RegionInsertPlan) -> list[dict]:
    """Mirror _apply_region's per-cluster faction_influence computation (DB-free)."""
    fi_overrides = plan.faction_influence_overrides
    total_sectors = int(plan.total_sectors or 0)
    if total_sectors <= 0:
        total_sectors = sum(int(cs.sector_count or 0) for cs in plan.clusters)
    out: list[dict] = []
    for cs in plan.clusters:
        midpoint = cluster_midpoint_sector(cs.sector_range_start, cs.sector_range_end)
        zone_type = zone_type_for_sector(plan.region_type, midpoint, total_sectors)
        out.append(build_cluster_faction_influence(zone_type, fi_overrides))
    return out


def test_extract_prefers_canon_faction_influence_key() -> None:
    assert _extract_faction_influence_overrides(None) is None
    assert _extract_faction_influence_overrides({}) is None
    assert _extract_faction_influence_overrides({"faction_influence": {}}) is None
    assert _extract_faction_influence_overrides({"faction_influence": "nope"}) is None

    canon = _extract_faction_influence_overrides(
        {"faction_influence": {"mercantile_guild": 200}}
    )
    assert canon == {"mercantile_guild": 200}

    alias = _extract_faction_influence_overrides(
        {"faction_influence_overrides": {"fringe_alliance": 50}}
    )
    assert alias == {"fringe_alliance": 50}

    # Canon key wins when both present.
    both = _extract_faction_influence_overrides(
        {
            "faction_influence": {"mercantile_guild": 200},
            "faction_influence_overrides": {"fringe_alliance": 50},
        }
    )
    assert both == {"mercantile_guild": 200}


def test_plan_none_overrides_match_pure_zone_profile() -> None:
    plan = RegionInsertPlan(
        region_type="terran_space",
        universe_seed=1,
        total_sectors=300,
        capital_sector_number=1,
        clusters=[
            _minimal_cluster(cluster_int_id=1, range_start=1, range_end=99),
            _minimal_cluster(cluster_int_id=2, range_start=100, range_end=201),
            _minimal_cluster(cluster_int_id=3, range_start=202, range_end=300),
        ],
        sectors=[],
        warps=[],
        stations=[],
        planets=[],
        formations=[],
        fedspace_sector_ints=[],
        special_location_by_sector={},
        raw_npc_rosters=[],
        raw_universe={},
        faction_influence_overrides=None,
    )
    seeded = _seed_cluster_influences(plan)
    assert len(seeded) == 3
    # Midpoints: 50→FED, 150→BORDER, 251→FRONTIER
    assert seeded[0] == build_cluster_faction_influence("FEDERATION")
    assert seeded[1] == build_cluster_faction_influence("BORDER")
    assert seeded[2] == build_cluster_faction_influence("FRONTIER")
    for influence in seeded:
        assert _numeric_sum(influence) == 100


def test_plan_overrides_change_cluster_influence_and_sum_100() -> None:
    overrides = {"mercantile_guild": 200}
    plan = RegionInsertPlan(
        region_type="terran_space",
        universe_seed=1,
        total_sectors=300,
        capital_sector_number=1,
        clusters=[
            _minimal_cluster(cluster_int_id=1, range_start=1, range_end=99),
            _minimal_cluster(cluster_int_id=2, range_start=202, range_end=300),
        ],
        sectors=[],
        warps=[],
        stations=[],
        planets=[],
        formations=[],
        fedspace_sector_ints=[],
        special_location_by_sector={},
        raw_npc_rosters=[],
        raw_universe={},
        faction_influence_overrides=overrides,
    )
    seeded = _seed_cluster_influences(plan)
    pure_fed = build_cluster_faction_influence("FEDERATION")
    pure_frontier = build_cluster_faction_influence("FRONTIER")

    assert seeded[0] != pure_fed
    assert seeded[1] != pure_frontier
    for influence in seeded:
        assert _numeric_sum(influence) == 100
        assert influence["dominant_faction"] == "mercantile_guild"
        assert influence["mercantile_guild"] == max(
            int(influence[k]) for k in FACTION_KEYS
        )

    # Extract → plan field path (region_metadata shape).
    meta = {"faction_influence": overrides, "region_id": "unused"}
    plan.faction_influence_overrides = _extract_faction_influence_overrides(meta)
    assert plan.faction_influence_overrides == overrides
    reseeded = _seed_cluster_influences(plan)
    assert reseeded[0] == seeded[0]
