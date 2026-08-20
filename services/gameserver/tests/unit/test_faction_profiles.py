"""Unit tests for bang-import step-15 faction-influence seeding (LEG-INI-02)."""
from __future__ import annotations

from src.core.faction_profiles import (
    TERRITORIAL_FACTIONS,
    ZONE_FACTION_PROFILES,
    area_weighted_galaxy_influence,
    influence_for_cluster,
    influence_for_zone,
    merge_influence_overrides,
    zone_ranges_for_region,
    zone_type_for_cluster_range,
)
from src.models.zone import ZoneType


def test_zone_profiles_sum_to_100_and_dominant():
    for zt, weights in ZONE_FACTION_PROFILES.items():
        assert sum(weights[k] for k in TERRITORIAL_FACTIONS) == 100, zt.name
        shaped = influence_for_zone(zt)
        assert sum(int(shaped[k]) for k in TERRITORIAL_FACTIONS) == 100
        expected_dom = max(TERRITORIAL_FACTIONS, key=lambda k: weights[k])
        assert shaped["dominant_faction"] == expected_dom


def test_federation_profile_is_federation_heavy():
    fed = influence_for_zone(ZoneType.FEDERATION)
    assert fed["terran_federation"] == 80
    assert fed["dominant_faction"] == "terran_federation"


def test_nexus_zone_ranges_are_expanse():
    ranges = zone_ranges_for_region("central_nexus", 5000)
    assert ranges == [(ZoneType.EXPANSE, 1, 5000)]
    assert (
        zone_type_for_cluster_range("central_nexus", 1, 250, 5000)
        == ZoneType.EXPANSE
    )


def test_terran_thirds_partition_and_cluster_assignment():
    ranges = zone_ranges_for_region("terran_space", 300)
    assert ranges[0][0] == ZoneType.FEDERATION
    assert ranges[0][1] == 1
    # first 33% of 300 = 99
    assert ranges[0][2] == 99
    assert ranges[-1][0] == ZoneType.FRONTIER
    assert ranges[-1][2] == 300

    assert zone_type_for_cluster_range("terran_space", 1, 50, 300) == ZoneType.FEDERATION
    # Mid-border cluster
    border = next(r for r in ranges if r[0] == ZoneType.BORDER)
    mid = (border[1] + border[2]) // 2
    assert (
        zone_type_for_cluster_range("terran_space", mid, mid, 300) == ZoneType.BORDER
    )
    assert (
        zone_type_for_cluster_range("terran_space", 280, 300, 300) == ZoneType.FRONTIER
    )


def test_influence_for_cluster_matches_zone():
    infl = influence_for_cluster("terran_space", 1, 40, 300)
    assert infl == influence_for_zone(ZoneType.FEDERATION)
    nexus = influence_for_cluster("central_nexus", 2251, 2500, 5000)
    assert nexus == influence_for_zone(ZoneType.EXPANSE)


def test_merge_overrides_renormalizes():
    base = influence_for_zone(ZoneType.BORDER)
    # Full six-key override (admin replaces the profile, then renormalize).
    merged = merge_influence_overrides(
        base,
        {
            "terran_federation": 90,
            "mercantile_guild": 10,
            "frontier_coalition": 0,
            "astral_mining_consortium": 0,
            "nova_scientific_institute": 0,
            "fringe_alliance": 0,
        },
    )
    assert sum(int(merged[k]) for k in TERRITORIAL_FACTIONS) == 100
    assert merged["dominant_faction"] == "terran_federation"
    assert merged["terran_federation"] == 90
    assert merged["mercantile_guild"] == 10

    # Partial override: named keys replace, then whole map renormalizes.
    partial = merge_influence_overrides(
        base, {"terran_federation": 90, "mercantile_guild": 10}
    )
    assert sum(int(partial[k]) for k in TERRITORIAL_FACTIONS) == 100
    assert partial["dominant_faction"] == "terran_federation"
    assert partial["terran_federation"] > base["terran_federation"]


def test_area_weighted_galaxy_influence():
    fed = influence_for_zone(ZoneType.FEDERATION)
    frontier = influence_for_zone(ZoneType.FRONTIER)
    # Equal area → average of the two profiles
    gal = area_weighted_galaxy_influence([(100, fed), (100, frontier)])
    assert gal["player_controlled"] == 0
    assert gal["contested"] == 0
    assert sum(int(gal[k]) for k in TERRITORIAL_FACTIONS) == 100
    # Federation weight should be midpoint of 80 and 5 = 42.5 → 42 or 43
    assert gal["terran_federation"] in (42, 43)
    # Heavier federation area pulls galaxy toward federation
    heavy = area_weighted_galaxy_influence([(900, fed), (100, frontier)])
    assert heavy["terran_federation"] > gal["terran_federation"]
