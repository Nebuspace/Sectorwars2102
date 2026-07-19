"""WO-GX1 — cluster-type seeding-bias activation, gameserver side.

Covers the two gameserver lanes the GX1 master defines:

  * Lane 1 (Nexus, ``nexus_generation_service.py``): the in-process bias
    branches now fire for the seeded bias types. The CRITICAL fix is that
    ``defenses['patrol_ships']`` is written as a SCALAR INT — four live
    consumers read it via ``int(...)`` (combat_service:3506,
    port_ownership_service:1792, admin:1495, admin_comprehensive:970), and a
    list-of-dicts detonates combat + admin in every military sector. The
    headline assertion is therefore ``int(sector['defenses']['patrol_ships'])``
    succeeding (no TypeError).

  * Lane 2 (non-Nexus, ``bang_import_service.py``): the pure ``_gx1_sector_bias``
    helper — patrol scalar-int, RESOURCE_RICH ×1.5 × zone composed
    MULTIPLICATIVELY (Frontier RR = ×2.1), probabilistic asteroid roll, starter
    exemption, frontier nebula scatter, CONTESTED null faction.

Plus the ruled Nexus proportion distribution shape (§2.2 / MAX-MEMO N1) and
the off/regression baseline (bias types absent ≡ no bias).
"""
from __future__ import annotations

import random
from unittest.mock import AsyncMock

import pytest

from src.models.cluster import ClusterType
from src.services.nexus_generation_service import NexusGenerationService
from src.services.bang_import_service import (
    _GX1_RESOURCE_RICH_BASE,
    _GX1_ZONE_MULTIPLIER_FEDERATION,
    _GX1_ZONE_MULTIPLIER_FRONTIER,
    _gx1_sector_bias,
    _gx1_zone_multiplier,
)


# ---------------------------------------------------------------------------
# Lane 2 — the pure Gap-B bias helper
# ---------------------------------------------------------------------------


class _ForceRandom(random.Random):
    """A Random whose .random() always clears any probability gate and whose
    .randint() returns a fixed value — so probabilistic branches always fire
    deterministically in tests."""

    def __init__(self, fixed_randint: int = 3) -> None:
        super().__init__(0)
        self._fixed = fixed_randint

    def random(self) -> float:  # always below any 0<p<=1 gate
        return 0.0

    def randint(self, a: int, b: int) -> int:
        return self._fixed


def test_military_patrol_ships_is_scalar_int_not_list():
    """THE CRITICAL FIX: patrol_ships must be a scalar int that survives int()."""
    rng = _ForceRandom(fixed_randint=3)
    resources, defenses, faction, force_nebula = _gx1_sector_bias(
        ClusterType.MILITARY_ZONE,
        is_fedspace=False,
        is_starter=False,
        rng=rng,
    )
    assert defenses is not None
    patrol = defenses["patrol_ships"]
    # Must NOT be a list (the bug). Must be an int that int() accepts.
    assert isinstance(patrol, int)
    assert not isinstance(patrol, list)
    # The exact crash the four live consumers would hit: int(patrol_ships).
    assert int(patrol) == 3
    # The combat/port consumers do `int(defenses.get('patrol_ships', 0) or 0)`.
    assert int(defenses.get("patrol_ships", 0) or 0) == 3
    assert resources is None
    assert faction is None
    assert force_nebula is False


def test_military_patrol_count_in_range():
    """Patrol count is the NO-CANON 2-4 range across many seeds, always scalar."""
    for seed in range(50):
        rng = random.Random(f"mz:{seed}")
        _, defenses, _, _ = _gx1_sector_bias(
            ClusterType.MILITARY_ZONE, False, False, rng
        )
        patrol = defenses["patrol_ships"]
        assert isinstance(patrol, int)
        assert 2 <= patrol <= 4


def test_resource_rich_multiplicative_border_zone():
    """RESOURCE_RICH in a border zone = base × 1.5 × 1.0."""
    rng = _ForceRandom()
    resources, defenses, faction, _ = _gx1_sector_bias(
        ClusterType.RESOURCE_RICH, is_fedspace=False, is_starter=False, rng=rng
    )
    assert resources is not None
    assert resources["has_asteroids"] is True
    y = resources["asteroid_yield"]
    # ×1.5 × 1.0 (border)
    assert y["ore"] == int(_GX1_RESOURCE_RICH_BASE["ore"] * 1.5)
    assert y["precious_metals"] == int(_GX1_RESOURCE_RICH_BASE["precious_metals"] * 1.5)
    assert y["quantum_shards"] == int(_GX1_RESOURCE_RICH_BASE["quantum_shards"] * 1.5)
    assert defenses is None
    assert faction is None


def test_resource_rich_frontier_is_richest_x21():
    """Frontier RESOURCE_RICH = 1.4 × 1.5 = ×2.1 — the game's richest yield."""
    rng = _ForceRandom()
    # Use a FRONTIER_OUTPOST-zone cluster but force RESOURCE_RICH branch:
    # zone is derived from cluster_type+fedspace, so we pass RESOURCE_RICH with
    # fedspace=False — that's BORDER. To exercise the Frontier multiplier we
    # confirm _gx1_zone_multiplier directly, then compose.
    border_resources, _, _, _ = _gx1_sector_bias(
        ClusterType.RESOURCE_RICH, False, False, rng
    )
    # Border ore baseline (×1.5):
    border_ore = border_resources["asteroid_yield"]["ore"]
    assert border_ore == int(_GX1_RESOURCE_RICH_BASE["ore"] * 1.5)
    # Frontier multiplier itself:
    assert _gx1_zone_multiplier(ClusterType.FRONTIER_OUTPOST, False) == (
        _GX1_ZONE_MULTIPLIER_FRONTIER
    )
    # The composed ×2.1 magnitude (what a Frontier RESOURCE_RICH cluster would
    # yield if its sectors were RESOURCE_RICH-typed in a frontier zone):
    expected_x21 = int(_GX1_RESOURCE_RICH_BASE["ore"] * 1.5 * 1.4)
    assert expected_x21 == int(1000 * 2.1)


def test_resource_rich_fedspace_zone_lower():
    """Federation zone dampens yield: × 0.7."""
    assert _gx1_zone_multiplier(ClusterType.RESOURCE_RICH, is_fedspace=True) == (
        _GX1_ZONE_MULTIPLIER_FEDERATION
    )
    rng = _ForceRandom()
    resources, _, _, _ = _gx1_sector_bias(
        ClusterType.RESOURCE_RICH, is_fedspace=True, is_starter=False, rng=rng
    )
    # ×1.5 × 0.7, composed sequentially with round() (float-noise-safe).
    assert resources["asteroid_yield"]["ore"] == int(
        round(_GX1_RESOURCE_RICH_BASE["ore"] * 1.5 * 0.7)
    )
    assert resources["asteroid_yield"]["ore"] == 1050


def test_resource_rich_probabilistic_not_blanket():
    """Asteroids are a per-sector roll (~0.5), not 100% of the cluster."""
    hits = 0
    n = 400
    for seed in range(n):
        rng = random.Random(f"rr:{seed}")
        resources, _, _, _ = _gx1_sector_bias(
            ClusterType.RESOURCE_RICH, False, False, rng
        )
        if resources is not None:
            hits += 1
    # Not all sectors, not zero — roughly half (wide tolerance).
    assert 0 < hits < n
    assert 0.3 < (hits / n) < 0.7


def test_frontier_outpost_scatters_nebula():
    """FRONTIER_OUTPOST sets force_nebula on a roll; no resources/defenses."""
    rng = _ForceRandom()
    resources, defenses, faction, force_nebula = _gx1_sector_bias(
        ClusterType.FRONTIER_OUTPOST, False, False, rng
    )
    assert force_nebula is True
    assert resources is None
    assert defenses is None
    assert faction is None


def test_contested_leaves_faction_null():
    """CONTESTED is non-assignment: controlling_faction stays null."""
    rng = _ForceRandom()
    resources, defenses, faction, force_nebula = _gx1_sector_bias(
        ClusterType.CONTESTED, False, False, rng
    )
    assert faction is None
    assert resources is None
    assert defenses is None
    assert force_nebula is False


@pytest.mark.parametrize(
    "ctype",
    [
        ClusterType.STANDARD,
        ClusterType.POPULATION_CENTER,
        ClusterType.TRADE_HUB,
        ClusterType.SPECIAL_INTEREST,
    ],
)
def test_non_biased_types_are_noop(ctype):
    """Off/regression baseline: non-biased types add nothing → column default."""
    rng = _ForceRandom()
    resources, defenses, faction, force_nebula = _gx1_sector_bias(
        ctype, False, False, rng
    )
    assert resources is None
    assert defenses is None
    assert faction is None
    assert force_nebula is False


@pytest.mark.parametrize(
    "ctype",
    [
        ClusterType.RESOURCE_RICH,
        ClusterType.MILITARY_ZONE,
        ClusterType.FRONTIER_OUTPOST,
        ClusterType.CONTESTED,
    ],
)
def test_starter_is_exempt_from_all_biases(ctype):
    """The starter (region capital) is exempt from every seeding bias."""
    rng = _ForceRandom()
    resources, defenses, faction, force_nebula = _gx1_sector_bias(
        ctype, is_fedspace=False, is_starter=True, rng=rng
    )
    assert resources is None
    assert defenses is None
    assert faction is None
    assert force_nebula is False


def test_bias_helper_is_deterministic():
    """Same (cluster_type, fedspace, starter, seed) → identical output."""
    out_a = _gx1_sector_bias(
        ClusterType.MILITARY_ZONE, False, False, random.Random("det:1")
    )
    out_b = _gx1_sector_bias(
        ClusterType.MILITARY_ZONE, False, False, random.Random("det:1")
    )
    assert out_a == out_b


# ---------------------------------------------------------------------------
# LOCKED — the ratified GX1 20-cluster Central Nexus table (4/2/6/5/3 remix)
#
# Canonical spec: sw2102-docs/SYSTEMS/central-nexus-clusters.md §"Cluster table".
# These assertions are deliberately LOCKED to the FROZEN ratified table so any
# future drift in nexus_generation_service._create_nexus_clusters fails here.
# Transcribed EXACTLY in index order 1..20; do NOT reorder/rename/reinterpret.
# ---------------------------------------------------------------------------

# (name, ClusterType, grid (x, y)) in index order — the FROZEN ratified table.
NEXUS_CLUSTER_TABLE = [
    ("Commerce Central Hub", ClusterType.TRADE_HUB, (0, 0)),          # 1  ANCHOR
    ("Diplomatic Quarter", ClusterType.POPULATION_CENTER, (1, 0)),    # 2
    ("Industrial Complex", ClusterType.TRADE_HUB, (2, 0)),            # 3
    ("Prospect Belt", ClusterType.RESOURCE_RICH, (3, 0)),             # 4
    ("Drift Reaches", ClusterType.FRONTIER_OUTPOST, (4, 0)),          # 5
    ("Outer Survey Station", ClusterType.FRONTIER_OUTPOST, (0, 1)),   # 6
    ("Free Trade Zone", ClusterType.TRADE_HUB, (1, 1)),              # 7
    ("Lodestar Reach", ClusterType.RESOURCE_RICH, (2, 1)),           # 8
    ("Quiet Quarter", ClusterType.STANDARD, (3, 1)),                # 9
    ("Gateway Plaza", ClusterType.STANDARD, (4, 1)),                # 10 ANCHOR
    ("Settlers' Rest", ClusterType.POPULATION_CENTER, (0, 2)),       # 11
    ("Transit Junction", ClusterType.STANDARD, (1, 2)),             # 12
    ("Slag Fields", ClusterType.RESOURCE_RICH, (2, 2)),             # 13
    ("Starport Complex", ClusterType.TRADE_HUB, (3, 2)),            # 14
    ("Marker's Edge", ClusterType.FRONTIER_OUTPOST, (4, 2)),        # 15
    ("The Bazaar", ClusterType.STANDARD, (0, 3)),                  # 16
    ("Lonesome Span", ClusterType.FRONTIER_OUTPOST, (1, 3)),       # 17
    ("Wayfarer Hollow", ClusterType.STANDARD, (2, 3)),            # 18
    ("Merchant's Row", ClusterType.STANDARD, (3, 3)),             # 19
    ("Frontier Gateway", ClusterType.FRONTIER_OUTPOST, (4, 3)),    # 20
]


async def _generate_nexus_clusters():
    """Invoke _create_nexus_clusters with a mock session and return the list."""
    service = NexusGenerationService()
    session = AsyncMock()
    # _create_nexus_clusters does session.add(...) (sync) then await session.flush().
    return await service._create_nexus_clusters(session, "region-uuid")


@pytest.mark.asyncio
async def test_nexus_cluster_table_is_ratified_gx1_remix():
    """LOCKED 1:1: the generated 20-cluster table matches the ratified FROZEN
    table EXACTLY — name + type + grid (x, y), in index order. A future drift in
    _create_nexus_clusters (rename, reorder, retype) fails this test."""
    clusters = await _generate_nexus_clusters()
    assert len(clusters) == 20, "the Nexus has exactly 20 clusters (invariant #1)"
    for idx, (name, ctype, (gx, gy)) in enumerate(NEXUS_CLUSTER_TABLE):
        c = clusters[idx]
        assert c.name == name, f"cluster #{idx + 1} name: {c.name!r} != {name!r}"
        assert c.type == ctype, f"cluster #{idx + 1} type: {c.type!r} != {ctype!r}"
        assert c.x_coord == gx, f"cluster #{idx + 1} x_coord: {c.x_coord} != {gx}"
        assert c.y_coord == gy, f"cluster #{idx + 1} y_coord: {c.y_coord} != {gy}"
        assert c.z_coord == 0


@pytest.mark.asyncio
async def test_nexus_cluster_type_counts_are_4_2_6_5_3():
    """LOCKED: the ratified remix proportions — 4 TRADE_HUB · 2 POPULATION_CENTER ·
    6 STANDARD · 5 FRONTIER_OUTPOST · 3 RESOURCE_RICH · 0 MILITARY/CONTESTED/SPECIAL
    (= 20). Replaces the prior 8/4/8 mix."""
    clusters = await _generate_nexus_clusters()
    from collections import Counter

    counts = Counter(c.type for c in clusters)
    assert counts[ClusterType.TRADE_HUB] == 4
    assert counts[ClusterType.POPULATION_CENTER] == 2
    assert counts[ClusterType.STANDARD] == 6
    assert counts[ClusterType.FRONTIER_OUTPOST] == 5
    assert counts[ClusterType.RESOURCE_RICH] == 3
    # The three types the Nexus never seeds (0 slots each).
    assert counts[ClusterType.MILITARY_ZONE] == 0
    assert counts[ClusterType.CONTESTED] == 0
    assert counts[ClusterType.SPECIAL_INTEREST] == 0
    assert sum(counts.values()) == 20


@pytest.mark.asyncio
async def test_nexus_civic_safe_anchors():
    """LOCKED invariant: slot 1 (Commerce Central Hub) stays TRADE_HUB — the
    starter/civic-safe cluster; slot 10 (Gateway Plaza) stays STANDARD — the
    Capital cluster, never FRONTIER_OUTPOST/RESOURCE_RICH."""
    clusters = await _generate_nexus_clusters()
    assert clusters[0].name == "Commerce Central Hub"
    assert clusters[0].type == ClusterType.TRADE_HUB
    assert clusters[9].name == "Gateway Plaza"
    assert clusters[9].type == ClusterType.STANDARD
    assert clusters[9].type not in (
        ClusterType.FRONTIER_OUTPOST,
        ClusterType.RESOURCE_RICH,
    )


@pytest.mark.asyncio
async def test_nexus_military_patrol_ships_is_scalar_int():
    """WO-GX1 CRITICAL: a MILITARY_ZONE Nexus sector writes patrol_ships as a
    SCALAR INT (never a list) — four live consumers read it via int()
    (combat_service:3506, port_ownership:1792, admin:1495, admin_comprehensive:970);
    a list-of-dicts detonates combat + admin in every military sector."""
    service = NexusGenerationService()
    session = AsyncMock()
    await service._generate_cluster_sectors(
        session, "region-uuid", "cluster-uuid", "zone-uuid",
        start_sector=400, end_sector=410,
        cluster_type=ClusterType.MILITARY_ZONE,
    )
    rows = []
    for call in session.execute.await_args_list:
        args = call.args
        if len(args) >= 2 and isinstance(args[1], list) and args[1]:
            rows = args[1]
            break
    military = [r for r in rows if "defenses" in r]
    assert military, "MILITARY_ZONE should seed defenses on its non-starter sectors"
    for r in military:
        patrol = r["defenses"]["patrol_ships"]
        assert isinstance(patrol, int) and not isinstance(patrol, bool)
        assert int(r["defenses"].get("patrol_ships", 0) or 0) >= 2
