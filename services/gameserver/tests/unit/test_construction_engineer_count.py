"""LEG-302: Space Engineer count wired into construction-event RNG."""

from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core import game_time
from src.models.colonist_profession import ProfessionType
from src.models.construction import ConstructionReservation
from src.services import construction_service as cs
from src.services import profession_service as ps


FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


class _PlanetIdQueryStub:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


def test_construction_engineer_count_zero_when_no_planets():
    db = SimpleNamespace(query=lambda target: _PlanetIdQueryStub([]))
    assert ps.construction_engineer_count(db, uuid4()) == 0


def test_construction_engineer_count_sums_across_planets_and_caps(monkeypatch):
    player_id = uuid4()
    planet_a, planet_b = uuid4(), uuid4()
    db = SimpleNamespace(
        query=lambda target: _PlanetIdQueryStub([(planet_a,), (planet_b,)])
    )

    def fake_counts(_db, planet_id):
        if planet_id == planet_a:
            return {ProfessionType.SPACE_ENGINEERS: 2}
        if planet_id == planet_b:
            return {ProfessionType.SPACE_ENGINEERS: 10}
        return {}

    monkeypatch.setattr(ps, "profession_counts", fake_counts)
    assert ps.construction_engineer_count(db, player_id) == ps.MAX_CONSTRUCTION_ENGINEERS_PER_PROJECT


class FakeRng:
    def __init__(self, fires: bool):
        self._random_value = 0.0 if fires else 0.99

    def random(self):
        return self._random_value

    def randint(self, a, b):
        return a

    def choice(self, seq):
        return seq[0]


def test_roll_construction_events_uses_db_resolved_engineer_count(monkeypatch):
    anchor = FIXED_NOW - timedelta(days=1)
    res = ConstructionReservation()
    res.total_cost = 40_000
    res.milestones = {}
    res.construction_events = []
    res.pending_events = []
    res.events_last_rolled_at = anchor
    res.player_id = uuid4()

    seen = {}

    def capture_count(_db, player_id):
        seen["player_id"] = player_id
        return 2

    monkeypatch.setattr(ps, "construction_engineer_count", capture_count)

    fired = cs._roll_construction_events(
        res,
        SimpleNamespace(),
        FIXED_NOW,
        rng=FakeRng(fires=True),
        db=object(),
    )
    assert fired == 1
    assert seen["player_id"] == res.player_id
    assert len(res.construction_events) == 1
