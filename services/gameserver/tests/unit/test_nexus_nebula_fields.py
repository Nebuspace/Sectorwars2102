"""WO-GWQ-NEXUS-NEBULA-FIELDS — nexus-generated NEBULA clusters get the same
canon nebula fields bang import derives, so quantum_service.harvest_nebula
stops rejecting every generator-made nebula as 'uncharted'.

bang_import_service._finalize_cluster_nebula_fields (WO-DBB-QR4 / WO-SB-QH2)
only fires at bang import; nexus_generation_service ALSO mints NEBULA
sectors (FRONTIER_OUTPOST scatter, ~:324/:393) but its Cluster(...)
constructor set no nebula_type/quantum_field_strength/color_hex, so
quantum_service.harvest_nebula rejected every generator-made nebula.

Covers:
  * _synthesize_cluster_nebula_fields (nexus_generation_service.py) — the
    pure per-cluster synthesis: zero NEBULA sectors leaves all three fields
    None; >=1 NEBULA sector derives a canon color + matching hex + a
    non-NULL 1-100 field strength, through the SAME shared
    derive_nebula_color / NEBULA_COLOR_HEX bang import uses
    (src.services.nebula_color — lifted out of bang_import_service.py by
    this WO so the boundary table has exactly one definition site).
  * _generate_cluster_sectors — the returned stats now carry
    "nebula_sectors" so the caller knows whether to synthesize.
  * quantum_service.harvest_nebula, fake session (house pattern from
    test_lumen_supply_chain.py): a Cluster carrying ONLY the
    generator-synthesized fields resolves a real harvest band instead of
    the 'not_a_nebula: uncharted' rejection.
  * Cluster.nebula_type/quantum_field_strength/color_hex stay nullable —
    this WO is comment-truth + generation-path only, zero schema change.
"""
from __future__ import annotations

import random
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.models.cluster import Cluster, ClusterType
from src.models.player import Player
from src.models.sector import Sector, SectorType
from src.models.ship import Ship
from src.services import quantum_service
from src.services.nebula_color import NEBULA_COLOR_HEX, derive_nebula_color
from src.services.nexus_generation_service import (
    NexusGenerationService,
    _synthesize_cluster_nebula_fields,
)

_CANON_COLORS = {"crimson", "azure", "emerald", "violet", "amber", "obsidian"}


def _blank_cluster() -> SimpleNamespace:
    """Column-default stand-in — all three nebula fields start None, exactly
    what nexus_generation_service.Cluster(...) leaves them at pre-WO."""
    return SimpleNamespace(nebula_type=None, quantum_field_strength=None, color_hex=None)


# ---------------------------------------------------------------------------
# _synthesize_cluster_nebula_fields — pure per-cluster synthesis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSynthesizeClusterNebulaFields:
    def test_zero_nebula_sectors_leaves_all_three_none(self) -> None:
        cluster = _blank_cluster()
        _synthesize_cluster_nebula_fields(cluster, nebula_sector_count=0)
        assert cluster.nebula_type is None
        assert cluster.quantum_field_strength is None
        assert cluster.color_hex is None

    def test_negative_count_is_treated_as_zero(self) -> None:
        cluster = _blank_cluster()
        _synthesize_cluster_nebula_fields(cluster, nebula_sector_count=-1)
        assert cluster.nebula_type is None
        assert cluster.quantum_field_strength is None
        assert cluster.color_hex is None

    def test_at_least_one_nebula_sector_derives_canon_color_and_hex(self) -> None:
        # Across many seeded rolls, every outcome must land on a real canon
        # color with its matching hex and a field strength inside 1-100 (the
        # shared boundary table's domain) — and the color must be EXACTLY
        # what derive_nebula_color would produce from that same roll, so the
        # roll and the color it produced never drift apart.
        for seed in range(60):
            random.seed(f"nexus-nebula:{seed}")
            cluster = _blank_cluster()
            _synthesize_cluster_nebula_fields(cluster, nebula_sector_count=1)

            assert cluster.nebula_type in _CANON_COLORS
            assert cluster.quantum_field_strength is not None
            assert 1.0 <= cluster.quantum_field_strength <= 100.0
            assert cluster.color_hex == NEBULA_COLOR_HEX[cluster.nebula_type]
            assert cluster.nebula_type == derive_nebula_color(cluster.quantum_field_strength)

    def test_nebula_sector_count_only_gates_presence_not_magnitude(self) -> None:
        # A cluster with 40 NEBULA sectors is synthesized the same way as one
        # with exactly 1 — the count only gates whether synthesis fires.
        cluster = _blank_cluster()
        _synthesize_cluster_nebula_fields(cluster, nebula_sector_count=40)
        assert cluster.nebula_type in _CANON_COLORS
        assert cluster.color_hex == NEBULA_COLOR_HEX[cluster.nebula_type]


# ---------------------------------------------------------------------------
# _generate_cluster_sectors — nebula_sectors count in returned stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateClusterSectorsNebulaCount:
    @pytest.mark.asyncio
    async def test_frontier_outpost_reports_nebula_sectors_when_forced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force EVERY probability gate to fire (random.random -> 0.0) so a
        FRONTIER_OUTPOST cluster's nebula_chance (0.15) always hits. None of
        sectors 600-609 is the global starter (sector_id 1), so all 10 hit."""
        monkeypatch.setattr(random, "random", lambda: 0.0)
        service = NexusGenerationService()
        session = AsyncMock()

        stats = await service._generate_cluster_sectors(
            session, "region-uuid", "cluster-uuid", "zone-uuid",
            start_sector=600, end_sector=609,
            cluster_type=ClusterType.FRONTIER_OUTPOST,
        )

        assert stats["sectors"] == 10
        assert stats["nebula_sectors"] == 10

    @pytest.mark.asyncio
    async def test_standard_cluster_never_reports_nebula_sectors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STANDARD clusters carry nebula_chance=0.0 — no forcing needed, but
        force random.random() -> 0.0 anyway to prove the gate (not luck) is
        what keeps this at zero."""
        monkeypatch.setattr(random, "random", lambda: 0.0)
        service = NexusGenerationService()
        session = AsyncMock()

        stats = await service._generate_cluster_sectors(
            session, "region-uuid", "cluster-uuid", "zone-uuid",
            start_sector=700, end_sector=709,
            cluster_type=ClusterType.STANDARD,
        )

        assert stats["nebula_sectors"] == 0


# ---------------------------------------------------------------------------
# End-to-end per-cluster contract: >=1 NEBULA sector -> all three fields set;
# zero NEBULA sectors -> all three stay None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClusterNebulaFieldsContract:
    @pytest.mark.asyncio
    async def test_frontier_cluster_with_nebula_sectors_gets_all_three_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(random, "random", lambda: 0.0)
        service = NexusGenerationService()
        session = AsyncMock()
        cluster = _blank_cluster()

        stats = await service._generate_cluster_sectors(
            session, "region-uuid", "cluster-uuid", "zone-uuid",
            start_sector=800, end_sector=805,
            cluster_type=ClusterType.FRONTIER_OUTPOST,
        )
        _synthesize_cluster_nebula_fields(cluster, stats["nebula_sectors"])

        assert stats["nebula_sectors"] > 0
        assert cluster.nebula_type in _CANON_COLORS
        assert cluster.quantum_field_strength is not None
        assert cluster.color_hex == NEBULA_COLOR_HEX[cluster.nebula_type]

    @pytest.mark.asyncio
    async def test_standard_cluster_with_zero_nebula_sectors_keeps_all_three_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(random, "random", lambda: 0.0)
        service = NexusGenerationService()
        session = AsyncMock()
        cluster = _blank_cluster()

        stats = await service._generate_cluster_sectors(
            session, "region-uuid", "cluster-uuid", "zone-uuid",
            start_sector=900, end_sector=905,
            cluster_type=ClusterType.STANDARD,
        )
        _synthesize_cluster_nebula_fields(cluster, stats["nebula_sectors"])

        assert stats["nebula_sectors"] == 0
        assert cluster.nebula_type is None
        assert cluster.quantum_field_strength is None
        assert cluster.color_hex is None


# ---------------------------------------------------------------------------
# quantum_service.harvest_nebula, fake session: a generator-synthesized
# cluster harvests through a real band instead of 'uncharted'.
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Stands in for a SQLAlchemy Query — filter()/populate_existing()/
    with_for_update() are no-ops that return self; first() returns the
    pre-wired result regardless of the filter predicate (house pattern from
    test_lumen_supply_chain.py)."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def filter(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._result


class _FakeSession:
    def __init__(self, rows: Dict[type, Any]) -> None:
        self._rows = rows

    def query(self, model: type) -> _FakeQuery:
        assert model in self._rows, f"unexpected query for {model!r}"
        return _FakeQuery(self._rows[model])

    def flush(self) -> None:
        pass


class _SeqRNG:
    """A fake `_RNG` exposing randint() (fixed) and random() (drawn from a
    supplied sequence) — enough values for both the crit roll and, for
    Emerald/Crimson, the Lumen roll (_roll_lumen_drop short-circuits before
    a second .random() call for every other color)."""

    def __init__(self, randint_value: int, random_values: List[float]) -> None:
        self._randint_value = randint_value
        self._random_iter = iter(random_values)

    def randint(self, lo: int, hi: int) -> int:
        return self._randint_value

    def random(self) -> float:
        return next(self._random_iter)


def _fake_player() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        turns=100,
        lifetime_turns_spent=0,
        quantum_shards=0,
        lumen_crystals=0,
        current_sector_id=1,
        is_docked=False,
        current_port_id=None,
        credits=0,
        lumen_refine_ready_at=None,
    )


def _fake_ship() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_destroyed=False,
        quantum_harvester_slot=True,
        quantum_harvest_cooldown_until=None,
    )


@pytest.mark.unit
class TestHarvestAcceptsGeneratorSynthesizedNebula:
    @pytest.fixture(autouse=True)
    def _no_op_collaborators(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Neuters the two collaborators harvest_nebula calls that would
        otherwise hit a real DB — neither is what this WO's tests prove."""
        monkeypatch.setattr(quantum_service, "regenerate_turns", lambda db, player: {})
        monkeypatch.setattr(
            quantum_service,
            "apply_emergent_action",
            lambda db, player, action, payload: None,
        )

    @pytest.mark.parametrize("field_strength", [5, 25, 45, 55, 70, 95])
    def test_generator_made_nebula_cluster_resolves_a_real_band(
        self, monkeypatch: pytest.MonkeyPatch, field_strength: int
    ) -> None:
        """A cluster carrying ONLY the nexus-synthesized fields must NOT hit
        the 'not_a_nebula: uncharted' rejection — the whole point of this WO."""
        color = derive_nebula_color(float(field_strength))
        player = _fake_player()
        ship = _fake_ship()
        player.current_ship = ship
        sector = SimpleNamespace(
            sector_id=player.current_sector_id, type=SectorType.NEBULA, cluster_id=1
        )
        cluster = SimpleNamespace(
            id=1, nebula_type=color, quantum_field_strength=float(field_strength),
            color_hex=NEBULA_COLOR_HEX[color],
        )
        db = _FakeSession({Player: player, Ship: ship, Sector: sector, Cluster: cluster})
        # Two values covers Emerald/Crimson's crit-then-lumen rolls; every
        # other color only consumes the first (short-circuited before a
        # second call — see _SeqRNG's docstring).
        monkeypatch.setattr(quantum_service, "_RNG", _SeqRNG(1, [0.99, 0.99]))

        result = quantum_service.harvest_nebula(db, player.id)

        assert result["nebula_type"] == color
        assert "shard_yield" in result

    def test_untouched_cluster_still_rejects_as_uncharted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: a cluster that never went through generation OR import
        (nebula_type still None — the pre-WO-DBB-QR4/WO-GX1 baseline) is
        still correctly rejected. This WO must not weaken that gate."""
        player = _fake_player()
        ship = _fake_ship()
        player.current_ship = ship
        sector = SimpleNamespace(
            sector_id=player.current_sector_id, type=SectorType.NEBULA, cluster_id=1
        )
        cluster = SimpleNamespace(
            id=1, nebula_type=None, quantum_field_strength=None, color_hex=None
        )
        db = _FakeSession({Player: player, Ship: ship, Sector: sector, Cluster: cluster})
        monkeypatch.setattr(quantum_service, "_RNG", _SeqRNG(1, [0.99, 0.99]))

        with pytest.raises(quantum_service.QuantumError, match="uncharted"):
            quantum_service.harvest_nebula(db, player.id)


# ---------------------------------------------------------------------------
# Cluster.nebula_type/quantum_field_strength/color_hex stay additive —
# comment-truth + generation-path only, zero schema change.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClusterNebulaColumnsRemainAdditive:
    def test_three_nebula_columns_stay_nullable(self) -> None:
        table = Cluster.__table__
        assert table.c.nebula_type.nullable is True
        assert table.c.quantum_field_strength.nullable is True
        assert table.c.color_hex.nullable is True
