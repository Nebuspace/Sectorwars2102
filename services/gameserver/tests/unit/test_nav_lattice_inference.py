"""WO-BUILD-COURSE-PLOTTING-LATTICE-INFERENCE — NavService lattice extension.

DB-free: monkeypatches graph construction + sector lookups so plot() can
exercise the Awakened+ conjectural-hop path without Postgres.
"""
from __future__ import annotations

import random
import uuid
from typing import Any, Dict, List, Set, Tuple
from unittest.mock import MagicMock

import pytest

from src.services.nav_service import (
    LATTICE_HOP_SUCCESS_BY_TIER,
    LATTICE_MIN_CONSCIOUSNESS,
    NavService,
)


def _player(*, level: int, sector: int = 1) -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.current_sector_id = sector
    p.aria_consciousness_level = level
    p.team_id = None
    return p


def _sector(sid: int, name: str | None = None) -> MagicMock:
    s = MagicMock()
    s.sector_id = sid
    s.id = uuid.uuid4()
    s.name = name or f"S{sid}"
    return s


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


@pytest.fixture
def nav_lattice(monkeypatch):
    """NavService whose known set is {1,2} and full graph is a line 1-2-3-4."""
    db = MagicMock()
    service = NavService(db)

    known = {1, 2}
    # full adjacency: 1↔2↔3↔4
    full_graph: Dict[int, List[Tuple[int, int, bool]]] = {
        1: [(2, 1, False)],
        2: [(1, 1, False), (3, 1, False)],
        3: [(2, 1, False), (4, 1, False)],
        4: [(3, 1, False)],
    }

    monkeypatch.setattr(NavService, "get_known_sector_ids", lambda self, p: set(known))
    monkeypatch.setattr(NavService, "_ring1_destination_ids", lambda self, ids: set())
    monkeypatch.setattr(NavService, "_build_safety_by_sid", lambda self, p: {1: 0.5, 2: 0.5})

    def fake_build(self, ids: Set[int]):
        # Filter full_graph to requested ids (mirrors known-filter behaviour)
        g = {
            sid: [(n, tc, vt) for (n, tc, vt) in neigh if n in ids]
            for sid, neigh in full_graph.items()
            if sid in ids
        }
        return g, {}

    monkeypatch.setattr(NavService, "_build_known_graph", fake_build)

    sectors = {sid: _sector(sid) for sid in (1, 2, 3, 4)}

    def query_side(model):
        name = getattr(model, "__name__", str(model))
        if name == "Sector":
            # plot() first queries target by sector_id; lattice queries all + hops
            q = MagicMock()
            def filter_fn(*args, **kwargs):
                # Heuristic: .first() path wants a single target
                q._mode = "filter"
                return q
            q.filter = filter_fn
            q.first = lambda: sectors.get(getattr(q, "_want", 4), sectors[4])
            q.all = lambda: [sectors[s] for s in (1, 2, 3, 4)]
            # sector_id column probe for .query(Sector.sector_id).all()
            if model is not type(sectors[1]) and hasattr(model, "key"):
                return _Query([(1,), (2,), (3,), (4,)])
            return q
        return MagicMock()

    # Simpler: patch the two query patterns lattice uses directly
    def query(model):
        # Sector.sector_id column object → all ids
        if getattr(model, "key", None) == "sector_id" or str(model).endswith("sector_id"):
            return _Query([(1,), (2,), (3,), (4,)])
        # Sector entity
        q = MagicMock()
        q.filter.return_value = q
        # first() used for target lookup — return sector 4 by default; tests
        # override via plot target
        q.first.return_value = sectors[4]
        q.all.return_value = list(sectors.values())
        return q

    db.query.side_effect = query
    return service, known, full_graph, sectors


@pytest.mark.parametrize("level", [1, 2])
def test_low_tier_uncharted_stays_unreachable(nav_lattice, level):
    service, *_ = nav_lattice
    player = _player(level=level, sector=1)
    # Force target lookup to sector 4
    result = service.plot(player, target_sector_id=4)
    assert result["reachable"] is False
    assert result.get("reason") == "uncharted"
    assert "lattice" not in result


def test_awakened_plots_lattice_into_unknown(nav_lattice):
    service, *_ = nav_lattice
    player = _player(level=3, sector=1)
    # Deterministic: all rolls succeed
    result = service._plot_with_lattice(
        player=player,
        start_sid=1,
        target_sector_id=4,
        target_sector=_sector(4),
        known_ids={1, 2},
        objective="min_time",
        aria_level=3,
        rng=random.Random(0),  # first randoms are low → success for 0.60
    )
    assert result is not None
    assert result["lattice"] is True
    assert result["lattice_tier"] == 3
    assert result["lattice_success_rate"] == LATTICE_HOP_SUCCESS_BY_TIER[3]
    # Path 1→2→3→4; hops exclude origin → 2,3,4
    assert [h["sector_id"] for h in result["hops"]] == [2, 3, 4]
    assert result["hops"][0]["conjectural"] is False  # 2 is known
    assert result["hops"][1]["conjectural"] is True
    assert result["hops"][2]["conjectural"] is True
    assert result["lattice_reached_target"] is True


def test_astray_stops_corridor_and_flags(nav_lattice):
    service, *_ = nav_lattice
    player = _player(level=3, sector=1)

    class AlwaysFail:
        def random(self):
            return 0.99  # always fail vs 0.60

        def choice(self, seq):
            return seq[0]

    result = service._plot_with_lattice(
        player=player,
        start_sid=1,
        target_sector_id=4,
        target_sector=_sector(4),
        known_ids={1, 2},
        objective="min_time",
        aria_level=3,
        rng=AlwaysFail(),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result["lattice_astray_count"] >= 1
    # Corridor aborts at first astray — may not reach target
    assert any(h["conjectural"] for h in result["hops"])


def test_lattice_min_consciousness_constant():
    assert LATTICE_MIN_CONSCIOUSNESS == 3
    assert set(LATTICE_HOP_SUCCESS_BY_TIER) == {3, 4, 5}
