"""Zone-type faction influence profiles for bang-import step 15.

Canon: ``sw2102-docs/SYSTEMS/bang-import-pipeline.md`` §15 — magnitudes are
fixed by the doc table; do not invent alternate weights.

Cluster JSONB shape (``DATA_MODELS/jsonb-schema.md``): six territorial faction
ints + ``dominant_faction``. Galaxy JSONB adds ``player_controlled`` /
``contested`` (absent on clusters; treated as 0 when averaging).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Six territorial allyable factions — order is the tie-break for argmax.
FACTION_KEYS: Tuple[str, ...] = (
    "terran_federation",
    "mercantile_guild",
    "frontier_coalition",
    "astral_mining_consortium",
    "nova_scientific_institute",
    "fringe_alliance",
)

GALAXY_EXTRA_KEYS: Tuple[str, ...] = ("player_controlled", "contested")

# Exact §15 table. Each row sums to 100.
ZONE_FACTION_PROFILES: Dict[str, Dict[str, int]] = {
    "FEDERATION": {
        "terran_federation": 80,
        "mercantile_guild": 15,
        "frontier_coalition": 0,
        "astral_mining_consortium": 2,
        "nova_scientific_institute": 2,
        "fringe_alliance": 1,
    },
    "BORDER": {
        "terran_federation": 30,
        "mercantile_guild": 30,
        "frontier_coalition": 20,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 5,
    },
    "FRONTIER": {
        "terran_federation": 5,
        "mercantile_guild": 10,
        "frontier_coalition": 60,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 10,
    },
    "EXPANSE": {
        "terran_federation": 25,
        "mercantile_guild": 25,
        "frontier_coalition": 15,
        "astral_mining_consortium": 15,
        "nova_scientific_institute": 10,
        "fringe_alliance": 10,
    },
}

# Failure mode: missing zone type → neutral (16/17 split, sum 100).
_NEUTRAL_WEIGHTS: Dict[str, int] = {
    "terran_federation": 17,
    "mercantile_guild": 17,
    "frontier_coalition": 17,
    "astral_mining_consortium": 17,
    "nova_scientific_institute": 16,
    "fringe_alliance": 16,
}


def zone_type_for_sector(
    region_type: str,
    sector_number: int,
    total_sectors: int,
) -> str:
    """Map a region-local sector number to a zone type (canon step 4).

    ``central_nexus`` → EXPANSE. ``terran_space`` / ``player_owned`` → thirds
    with rounding that favours FEDERATION on the low end and FRONTIER on the
    high end so ranges are contiguous and cover ``[1, total_sectors]``.
    """
    if region_type == "central_nexus":
        return "EXPANSE"
    if total_sectors <= 0:
        return "BORDER"
    # 33% / 34% / 33% with low/high favouring.
    fed_end = (total_sectors * 33) // 100
    if fed_end < 1:
        fed_end = 1
    frontier_count = (total_sectors * 33) // 100
    if frontier_count < 1:
        frontier_count = 1
    frontier_start = total_sectors - frontier_count + 1
    if frontier_start <= fed_end:
        # Tiny regions: collapse to a single contiguous split.
        frontier_start = fed_end + 1
    if sector_number <= fed_end:
        return "FEDERATION"
    if sector_number >= frontier_start:
        return "FRONTIER"
    return "BORDER"


def profile_for_zone_type(zone_type: str) -> Dict[str, int]:
    """Return the six-faction weight dict for a zone type (copy)."""
    key = (zone_type or "").upper()
    base = ZONE_FACTION_PROFILES.get(key)
    if base is None:
        return dict(_NEUTRAL_WEIGHTS)
    return dict(base)


def dominant_faction(weights: Mapping[str, Any]) -> str:
    """Argmax over FACTION_KEYS; ties → first in FACTION_KEYS order."""
    best_key = FACTION_KEYS[0]
    best_val = int(weights.get(best_key, 0) or 0)
    for key in FACTION_KEYS[1:]:
        val = int(weights.get(key, 0) or 0)
        if val > best_val:
            best_key = key
            best_val = val
    return best_key


def _renormalize_ints(weights: Mapping[str, float], keys: Sequence[str]) -> Dict[str, int]:
    """Largest-remainder renormalization so ints over ``keys`` sum to 100."""
    raw = {k: max(0.0, float(weights.get(k, 0) or 0)) for k in keys}
    total = sum(raw.values())
    if total <= 0:
        # Degenerate: equal split.
        n = len(keys)
        base = 100 // n
        out = {k: base for k in keys}
        rem = 100 - base * n
        for k in keys:
            if rem <= 0:
                break
            out[k] += 1
            rem -= 1
        return out
    exact = {k: (raw[k] * 100.0) / total for k in keys}
    floors = {k: int(exact[k]) for k in keys}
    rem = 100 - sum(floors.values())
    # Distribute remainder by largest fractional parts.
    fracs = sorted(
        ((exact[k] - floors[k], k) for k in keys),
        key=lambda t: (-t[0], keys.index(t[1])),
    )
    out = dict(floors)
    for i in range(rem):
        out[fracs[i % len(fracs)][1]] += 1
    return out


def merge_overrides_renormalize(
    base: Mapping[str, Any],
    overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Merge admin override weights into ``base`` and renormalize to sum 100."""
    merged: Dict[str, float] = {k: float(base.get(k, 0) or 0) for k in FACTION_KEYS}
    if overrides:
        for k, v in overrides.items():
            if k in FACTION_KEYS and v is not None:
                merged[k] = float(v)
    return _renormalize_ints(merged, FACTION_KEYS)


def build_cluster_faction_influence(
    zone_type: str,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build Cluster.faction_influence JSONB for a zone type (+ optional overrides)."""
    profile = profile_for_zone_type(zone_type)
    weights = merge_overrides_renormalize(profile, overrides)
    # Preserve contested dominant for untouched neutral profile.
    if overrides:
        dom = dominant_faction(weights)
    else:
        if zone_type.upper() not in ZONE_FACTION_PROFILES:
            dom = "contested"
        else:
            dom = dominant_faction(weights)
    out: Dict[str, Any] = dict(weights)
    out["dominant_faction"] = dom
    return out


def galaxy_area_weighted_average(
    clusters: Iterable[Tuple[int, Mapping[str, Any]]],
) -> Dict[str, int]:
    """Area-weighted average of cluster influence → Galaxy.faction_influence.

    ``clusters`` is ``(sector_count, faction_influence_dict)``. Extra galaxy
    keys ``player_controlled`` / ``contested`` default to 0 (clusters omit them).
    """
    keys = FACTION_KEYS + GALAXY_EXTRA_KEYS
    acc = {k: 0.0 for k in keys}
    total_weight = 0
    for sector_count, influence in clusters:
        w = max(0, int(sector_count or 0))
        if w <= 0:
            continue
        total_weight += w
        for k in FACTION_KEYS:
            acc[k] += w * float(influence.get(k, 0) or 0)
        for k in GALAXY_EXTRA_KEYS:
            acc[k] += w * float(influence.get(k, 0) or 0)
    if total_weight <= 0:
        return _renormalize_ints({k: 0.0 for k in keys}, keys)
    averaged = {k: acc[k] / total_weight for k in keys}
    return _renormalize_ints(averaged, keys)


def cluster_midpoint_sector(range_start: int, range_end: int) -> int:
    """Inclusive midpoint of a cluster sector range (used for zone lookup)."""
    lo = int(range_start)
    hi = int(range_end)
    if hi < lo:
        lo, hi = hi, lo
    return (lo + hi) // 2
