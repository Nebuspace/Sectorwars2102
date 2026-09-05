"""LEG-3599: per-project Space Engineer assign/unassign API."""

from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core import game_time
from src.models.colonist_profession import ProfessionType
from src.models.construction import ConstructionReservation
from src.services import construction_service as cs
from src.services import profession_service as ps
from src.services.construction_service import ConstructionError

FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


def _player(player_id=None):
    return SimpleNamespace(id=player_id or uuid4())


def _reservation(player_id=None, state="frame_assembly"):
    res = ConstructionReservation()
    res.id = uuid4()
    res.player_id = player_id or uuid4()
    res.state = state
    res.assigned_engineers = []
    res.total_cost = 40_000
    res.milestones = {}
    res.construction_events = []
    res.pending_events = []
    return res


class _PlanetOwnerQuery:
    def __init__(self, owned):
        self._owned = owned

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return (self._owned,) if self._owned else None


class _PeerReservationQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


def test_assigned_construction_engineer_count_empty():
    res = _reservation()
    db = SimpleNamespace()
    assert cs.assigned_construction_engineer_count(db, res) == 0


def test_assigned_construction_engineer_count_sums_and_caps():
    res = _reservation()
    planet_a, planet_b = str(uuid4()), str(uuid4())
    res.assigned_engineers = [
        {"planet_id": planet_a, "count": 2},
        {"planet_id": planet_b, "count": 2},
    ]
    db = SimpleNamespace()
    assert cs.assigned_construction_engineer_count(db, res) == ps.MAX_CONSTRUCTION_ENGINEERS_PER_PROJECT


def test_assign_engineer_happy_path(monkeypatch):
    player = _player()
    planet_id = uuid4()
    res = _reservation(player_id=player.id)

    monkeypatch.setattr(
        cs,
        "_planet_assigned_on_other_reservations",
        lambda *a, **k: 0,
    )
    monkeypatch.setattr(ps, "space_engineers_on_planet", lambda db, pid: 5)

    db = SimpleNamespace(
        query=lambda target: _PlanetOwnerQuery(True),
        flush=lambda: None,
    )

    result = cs.assign_engineer(db, res, player, planet_id, count=2, now=FIXED_NOW)
    assert result["assigned_count"] == 2
    assert result["assigned_engineer_count"] == 2
    assert res.assigned_engineers == [{"planet_id": str(planet_id), "count": 2}]


def test_assign_engineer_rejects_over_cap(monkeypatch):
    player = _player()
    planet_id = uuid4()
    res = _reservation(player_id=player.id)
    res.assigned_engineers = [{"planet_id": str(uuid4()), "count": 2}]

    monkeypatch.setattr(
        cs,
        "_planet_assigned_on_other_reservations",
        lambda *a, **k: 0,
    )
    monkeypatch.setattr(ps, "space_engineers_on_planet", lambda db, pid: 10)

    db = SimpleNamespace(
        query=lambda target: _PlanetOwnerQuery(True),
        flush=lambda: None,
    )

    with pytest.raises(ConstructionError) as exc:
        cs.assign_engineer(db, res, player, planet_id, count=2, now=FIXED_NOW)
    assert exc.value.status_code == 400
    assert "At most" in exc.value.detail


def test_assign_engineer_rejects_insufficient_planet_pool(monkeypatch):
    player = _player()
    planet_id = uuid4()
    res = _reservation(player_id=player.id)

    monkeypatch.setattr(
        cs,
        "_planet_assigned_on_other_reservations",
        lambda *a, **k: 1,
    )
    monkeypatch.setattr(ps, "space_engineers_on_planet", lambda db, pid: 2)

    db = SimpleNamespace(
        query=lambda target: _PlanetOwnerQuery(True),
        flush=lambda: None,
    )

    with pytest.raises(ConstructionError) as exc:
        cs.assign_engineer(db, res, player, planet_id, count=2, now=FIXED_NOW)
    assert exc.value.status_code == 400
    assert "available" in exc.value.detail


def test_assign_engineer_rejects_terminal_state():
    player = _player()
    res = _reservation(player_id=player.id, state="claimed")
    db = SimpleNamespace(query=lambda target: _PlanetOwnerQuery(True), flush=lambda: None)

    with pytest.raises(ConstructionError) as exc:
        cs.assign_engineer(db, res, player, uuid4(), now=FIXED_NOW)
    assert exc.value.status_code == 400


def test_unassign_engineer_happy_path():
    player = _player()
    planet_id = uuid4()
    res = _reservation(player_id=player.id)
    res.assigned_engineers = [{"planet_id": str(planet_id), "count": 2}]
    db = SimpleNamespace(flush=lambda: None)

    result = cs.unassign_engineer(db, res, player, planet_id, count=1, now=FIXED_NOW)
    assert result["unassigned_count"] == 1
    assert result["assigned_engineer_count"] == 1
    assert res.assigned_engineers == [{"planet_id": str(planet_id), "count": 1}]


def test_unassign_engineer_rejects_when_none_assigned():
    player = _player()
    planet_id = uuid4()
    res = _reservation(player_id=player.id)
    db = SimpleNamespace(flush=lambda: None)

    with pytest.raises(ConstructionError) as exc:
        cs.unassign_engineer(db, res, player, planet_id, now=FIXED_NOW)
    assert exc.value.status_code == 400


def test_planet_assigned_on_other_reservations():
    player_id = uuid4()
    planet_id = uuid4()
    current = _reservation(player_id=player_id)
    sibling = _reservation(player_id=player_id, state="outfitting")
    sibling.assigned_engineers = [{"planet_id": str(planet_id), "count": 2}]
    other_planet = _reservation(player_id=player_id, state="queued")
    other_planet.assigned_engineers = [{"planet_id": str(uuid4()), "count": 1}]

    db = SimpleNamespace(
        query=lambda target: _PeerReservationQuery([sibling, other_planet])
    )
    assigned = cs._planet_assigned_on_other_reservations(
        db, player_id, planet_id, current.id
    )
    assert assigned == 2


class FakeRng:
    def __init__(self, fires: bool):
        self._random_value = 0.0 if fires else 0.99

    def random(self):
        return self._random_value

    def randint(self, a, b):
        return a

    def choice(self, seq):
        return seq[0]


def test_roll_construction_events_uses_assigned_count(monkeypatch):
    anchor = FIXED_NOW - timedelta(days=1)
    res = _reservation()
    res.events_last_rolled_at = anchor
    res.assigned_engineers = [{"planet_id": str(uuid4()), "count": 2}]

    seen = {}

    def capture_count(_db, reservation):
        seen["reservation_id"] = reservation.id
        return 2

    monkeypatch.setattr(cs, "assigned_construction_engineer_count", capture_count)

    fired = cs._roll_construction_events(
        res,
        SimpleNamespace(),
        FIXED_NOW,
        rng=FakeRng(fires=True),
        db=object(),
    )
    assert fired == 1
    assert seen["reservation_id"] == res.id


def test_roll_construction_events_zero_when_unassigned():
    anchor = FIXED_NOW - timedelta(days=1)
    res = _reservation()
    res.events_last_rolled_at = anchor
    res.assigned_engineers = []

    prob_unassigned = cs.event_fires_today(engineer_count=0, rng=FakeRng(fires=True))
    prob_assigned = cs.event_fires_today(engineer_count=2, rng=FakeRng(fires=True))
    assert prob_assigned >= prob_unassigned
