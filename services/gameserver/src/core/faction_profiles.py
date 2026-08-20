"""Per-zone faction-influence profiles for bang-import step 15 (LEG-INI-02).

Canon: ``sw2102-docs/SYSTEMS/bang-import-pipeline.md`` §15 + Appendix F —
``Cluster.faction_influence`` is **not** emitted by bang; the gameserver
seeds it from a per-zone-type default profile. ``Galaxy.faction_influence``
is the area-weighted average of all clusters.

Zone rows are orthogonal to clusters and may be absent on the bang-import
path today — zone type is derived from region context + sector-range
partition (same thirds as pipeline step 4).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from src.models.zone import ZoneType

# Six territorial allyable factions only (DATA_MODELS/jsonb-schema.md §
# Cluster.faction_influence). Shadow Syndicate / pirates / cabal excluded.
TERRITORIAL_FACTIONS: Tuple[str, ...] = (
    "terran_federation",
    "mercantile_guild",
    "frontier_coalition",
    "astral_mining_consortium",
    "nova_scientific_institute",
    "fringe_alliance",
)

# Canon bang-import-pipeline.md §15 table — each row sums to 100.
ZONE_FACTION_PROFILES: Dict[ZoneType, Dict[str, int]] = {
    ZoneType.FEDERATION: {
        "terran_federation": 80,
        "mercantile_guild": 15,
        "frontier_coalition": 0,
        "astral_mining_consortium": 2,
        "nova_scientific_institute": 2,
        "fringe_alliance": 1,
    },
    ZoneType.BORDER: {
        "terran_federation": 30,
        "mercantile_guild": 30,
        "frontier_coalition": 20,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 5,
    },
    ZoneType.FRONTIER: {
        "terran_federation": 5,
        "mercantile_guild": 10,
        "frontier_coalition": 60,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 10,
    },
    ZoneType.EXPANSE: {
        "terran_federation": 25,
        "mercantile_guild": 25,
        "frontier_coalition": 15,
        "astral_mining_consortium": 15,
        "nova_scientific_institute": 10,
        "fringe_alliance": 10,
    },
}


def dominant_faction(weights: Mapping[str, Union[int, float]]) -> str:
    """Return the faction key with the highest numeric weight (argmax).

    Tie-break: first among ``TERRITORIAL_FACTIONS`` order when weights equal.
    """
    best_key = TERRITORIAL_FACTIONS[0]
    best_val = float("-inf")
    for key in TERRITORIAL_FACTIONS:
        val = float(weights.get(key, 0) or 0)
        if val > best_val:
            best_val = val
            best_key = key
    return best_key


def with_dominant(weights: Mapping[str, Union[int, float]]) -> Dict[str, Any]:
    """Copy territorial weights + ``dominant_faction`` for Cluster JSONB."""
    out: Dict[str, Any] = {k: int(round(float(weights.get(k, 0) or 0))) for k in TERRITORIAL_FACTIONS}
    # Re-normalize rounding drift so numerics still sum to 100 when possible.
    total = sum(out[k] for k in TERRITORIAL_FACTIONS)
    if total != 100 and total > 0:
        # Nudge the dominant key to absorb 1-point rounding error.
        dom = dominant_faction(out)
        out[dom] = out[dom] + (100 - total)
    out["dominant_faction"] = dominant_faction(out)
    return out


def influence_for_zone(zone_type: ZoneType) -> Dict[str, Any]:
    """Return Cluster-shaped faction_influence for a zone profile."""
    return with_dominant(ZONE_FACTION_PROFILES[zone_type])


def zone_ranges_for_region(
    region_type: str, total_sectors: int
) -> List[Tuple[ZoneType, int, int]]:
    """Return inclusive (zone_type, start, end) ranges for a region.

    Mirrors bang-import-pipeline.md step 4. Rounding favours FEDERATION on
    the low end and FRONTIER on the high end; middle is BORDER.
    """
    if total_sectors < 1:
        return []
    if region_type == "central_nexus":
        return [(ZoneType.EXPANSE, 1, total_sectors)]

    # terran_space / player_owned (and any other three-zone region)
    fed_end = max(1, int(total_sectors * 0.33))
    # Middle 34%: start after fed, length ≈ 0.34 * N
    border_len = max(0, int(round(total_sectors * 0.34)))
    border_start = fed_end + 1
    border_end = min(total_sectors, border_start + border_len - 1) if border_start <= total_sectors else fed_end
    if border_start > total_sectors:
        return [(ZoneType.FEDERATION, 1, total_sectors)]
    frontier_start = border_end + 1
    ranges: List[Tuple[ZoneType, int, int]] = [
        (ZoneType.FEDERATION, 1, fed_end),
    ]
    if border_start <= border_end:
        ranges.append((ZoneType.BORDER, border_start, border_end))
    if frontier_start <= total_sectors:
        ranges.append((ZoneType.FRONTIER, frontier_start, total_sectors))
    return ranges


def _overlap_len(a0: int, a1: int, b0: int, b1: int) -> int:
    lo = max(a0, b0)
    hi = min(a1, b1)
    return max(0, hi - lo + 1)


def zone_type_for_cluster_range(
    region_type: str,
    sector_range_start: int,
    sector_range_end: int,
    total_sectors: int,
) -> ZoneType:
    """Pick the zone whose range covers the majority of the cluster range.

    Tie-break: zone containing the cluster midpoint sector.
    """
    ranges = zone_ranges_for_region(region_type, total_sectors)
    if not ranges:
        return ZoneType.BORDER
    start = int(sector_range_start)
    end = int(sector_range_end)
    if end < start:
        start, end = end, start

    best: Optional[Tuple[int, ZoneType]] = None
    for zt, z0, z1 in ranges:
        ov = _overlap_len(start, end, z0, z1)
        if best is None or ov > best[0]:
            best = (ov, zt)
    if best is not None and best[0] > 0:
        # Check for ties at the same overlap — use midpoint.
        top = best[0]
        tied = [zt for zt, z0, z1 in ranges if _overlap_len(start, end, z0, z1) == top]
        if len(tied) == 1:
            return tied[0]
        mid = (start + end) // 2
        for zt, z0, z1 in ranges:
            if z0 <= mid <= z1:
                return zt
        return tied[0]
    # No overlap (degenerate ranges): midpoint fallback.
    mid = (start + end) // 2
    for zt, z0, z1 in ranges:
        if z0 <= mid <= z1:
            return zt
    return ranges[0][0]


def merge_influence_overrides(
    base: Mapping[str, Any],
    overrides: Optional[Mapping[str, Union[int, float]]],
) -> Dict[str, Any]:
    """Merge admin faction_influence overrides, renormalize to sum 100."""
    weights: Dict[str, float] = {
        k: float(base.get(k, 0) or 0) for k in TERRITORIAL_FACTIONS
    }
    if overrides:
        for k, v in overrides.items():
            if k in TERRITORIAL_FACTIONS and v is not None:
                weights[k] = float(v)
    total = sum(weights.values())
    if total <= 0:
        return influence_for_zone(ZoneType.BORDER)
    scaled = {k: (weights[k] / total) * 100.0 for k in TERRITORIAL_FACTIONS}
    return with_dominant(scaled)


def influence_for_cluster(
    region_type: str,
    sector_range_start: int,
    sector_range_end: int,
    total_sectors: int,
    overrides: Optional[Mapping[str, Union[int, float]]] = None,
) -> Dict[str, Any]:
    """Full step-15 seed for one cluster (profile + optional overrides)."""
    zt = zone_type_for_cluster_range(
        region_type, sector_range_start, sector_range_end, total_sectors
    )
    return merge_influence_overrides(influence_for_zone(zt), overrides)


def area_weighted_galaxy_influence(
    clusters: Iterable[Tuple[int, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Area-weighted average of cluster influences → Galaxy JSONB shape.

    ``clusters`` is an iterable of ``(sector_count, influence_dict)``.
    Galaxy schema also lists ``player_controlled`` / ``contested``; seeded
    to 0 so the six territorial weights remain the sum-to-100 mass.
    """
    weighted: Dict[str, float] = {k: 0.0 for k in TERRITORIAL_FACTIONS}
    total_sectors = 0
    for sector_count, infl in clusters:
        n = max(0, int(sector_count or 0))
        if n <= 0:
            continue
        total_sectors += n
        for k in TERRITORIAL_FACTIONS:
            weighted[k] += float(infl.get(k, 0) or 0) * n
    if total_sectors <= 0:
        base = influence_for_zone(ZoneType.BORDER)
        return {
            **{k: int(base[k]) for k in TERRITORIAL_FACTIONS},
            "player_controlled": 0,
            "contested": 0,
        }
    avg = {k: weighted[k] / total_sectors for k in TERRITORIAL_FACTIONS}
    shaped = with_dominant(avg)
    return {
        **{k: int(shaped[k]) for k in TERRITORIAL_FACTIONS},
        "player_controlled": 0,
        "contested": 0,
    }
