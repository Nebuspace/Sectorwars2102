"""LEG-136: zone-profile faction influence seeding (bang-import §15)."""
from __future__ import annotations

import pytest

from src.core.faction_profiles import (
    FACTION_KEYS,
    ZONE_FACTION_PROFILES,
    build_cluster_faction_influence,
    galaxy_area_weighted_average,
    merge_overrides_renormalize,
    profile_for_zone_type,
    zone_type_for_sector,
)


def _numeric_sum(influence: dict) -> int:
    return sum(int(influence[k]) for k in FACTION_KEYS)


@pytest.mark.parametrize("zone_type", list(ZONE_FACTION_PROFILES.keys()))
def test_zone_profiles_sum_to_100_and_dominant(zone_type: str) -> None:
    influence = build_cluster_faction_influence(zone_type)
    assert _numeric_sum(influence) == 100
    expected = max(ZONE_FACTION_PROFILES[zone_type], key=ZONE_FACTION_PROFILES[zone_type].get)
    assert influence["dominant_faction"] == expected
    for k in FACTION_KEYS:
        assert influence[k] == ZONE_FACTION_PROFILES[zone_type][k]


def test_unknown_zone_uses_neutral_contested() -> None:
    influence = build_cluster_faction_influence("NOT_A_ZONE")
    assert _numeric_sum(influence) == 100
    assert influence["dominant_faction"] == "contested"
    assert influence["terran_federation"] == 17
    assert influence["fringe_alliance"] == 16


def test_merge_overrides_renormalize() -> None:
    base = profile_for_zone_type("FEDERATION")
    # Force mercantile to dominate, then renormalize.
    merged = merge_overrides_renormalize(base, {"mercantile_guild": 200})
    assert sum(merged.values()) == 100
    assert merged["mercantile_guild"] == max(merged.values())
    influence = build_cluster_faction_influence("FEDERATION", {"mercantile_guild": 200})
    assert _numeric_sum(influence) == 100
    assert influence["dominant_faction"] == "mercantile_guild"


def test_zone_partition_terran_300() -> None:
    n = 300
    # 33% → 99; frontier last 99 → start 202; border 100–201.
    assert zone_type_for_sector("terran_space", 1, n) == "FEDERATION"
    assert zone_type_for_sector("terran_space", 99, n) == "FEDERATION"
    assert zone_type_for_sector("terran_space", 100, n) == "BORDER"
    assert zone_type_for_sector("terran_space", 201, n) == "BORDER"
    assert zone_type_for_sector("terran_space", 202, n) == "FRONTIER"
    assert zone_type_for_sector("terran_space", 300, n) == "FRONTIER"
    assert zone_type_for_sector("central_nexus", 2500, 5000) == "EXPANSE"
    assert zone_type_for_sector("player_owned", 50, n) == "FEDERATION"


def test_galaxy_area_weighted_average_fixture() -> None:
    fed = build_cluster_faction_influence("FEDERATION")
    frontier = build_cluster_faction_influence("FRONTIER")
    # Equal area → arithmetic mean of the two profiles (ints, renormalized).
    avg = galaxy_area_weighted_average([(100, fed), (100, frontier)])
    assert sum(avg.values()) == 100
    # terran: (80+5)/2 = 42.5 → 42 or 43 after remainder; frontier: (0+60)/2 = 30
    assert avg["frontier_coalition"] == 30
    assert avg["terran_federation"] in (42, 43)
    assert avg["player_controlled"] == 0
    assert avg["contested"] == 0

    # Weighting: 3× FEDERATION + 1× FRONTIER
    weighted = galaxy_area_weighted_average([(300, fed), (100, frontier)])
    assert sum(weighted.values()) == 100
    # terran ≈ (80*300 + 5*100)/400 = 61.25
    assert weighted["terran_federation"] in (61, 62)
