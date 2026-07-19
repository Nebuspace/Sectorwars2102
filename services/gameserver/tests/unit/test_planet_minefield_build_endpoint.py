"""Tests for WO-P5-planets-minefield-wiring's build-endpoint half:
planet_minefield was defined-but-unreachable (citadel_service.DEFENSE_BUILDINGS
had the catalog entry since WO-G7, but routes/planets.py's ConstructBuildingRequest
regex never allowed the type through, and no test exercised
CitadelService.build_defense_building's generic queue/settle flow for it).

Two halves:
  (1) the route-level Pydantic gate now accepts "planet_minefield".
  (2) CitadelService.build_defense_building / _settle_build_queue — already
      fully generic/data-driven (confirmed by reading the source: validated
      only against DEFENSE_BUILDINGS, no type-specific branching) — behave
      correctly for planet_minefield's specific citadel-level gate (3+) and
      capacity ladder (1@L3 / 2@L4 / 3@L5, defense.md).

FakeSession mirrors test_research_unlock_route.py's _FakeQuery/_FakeSession
shape, extended to route by SQLAlchemy model class since build_defense_building
queries both Planet and Player in one call.
"""
import uuid
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.api.routes.planets import ConstructBuildingRequest
from src.models.planet import Planet
from src.models.player import Player
from src.services.citadel_service import CitadelService, DEFENSE_BUILDINGS


# --------------------------------------------------------------------------- #
# (1) Route-level Pydantic gate.
# --------------------------------------------------------------------------- #

def test_route_request_model_now_accepts_planet_minefield():
    req = ConstructBuildingRequest(buildingType="planet_minefield")
    assert req.buildingType == "planet_minefield"


def test_route_request_model_still_rejects_garbage_types():
    with pytest.raises(Exception):
        ConstructBuildingRequest(buildingType="not_a_real_building")


# --------------------------------------------------------------------------- #
# (2) CitadelService.build_defense_building / _settle_build_queue.
# --------------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._obj


class _FakeSession:
    """Routes db.query(Planet) / db.query(Player) to fixed fixture rows —
    build_defense_building's only two query targets."""

    def __init__(self, planet, player):
        self._planet = planet
        self._player = player
        self.flush_count = 0

    def query(self, model):
        if model is Planet:
            return _FakeQuery(self._planet)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query model: {model}")

    def flush(self):
        self.flush_count += 1


def _planet(*, citadel_level, defense_buildings=None, owner_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        active_events={"defense_buildings": dict(defense_buildings or {})},
    )


def _player(*, credits=1_000_000):
    return SimpleNamespace(id=uuid.uuid4(), credits=credits)


def _build(planet, player, building_type="planet_minefield"):
    db = _FakeSession(planet, player)
    svc = CitadelService(db)
    return svc.build_defense_building(planet.id, player.id, building_type)


def test_planet_minefield_spec_matches_canon_ladder():
    """Pin the WO-G7 catalog entry the resolver/build-endpoint both rely on
    (defense.md §"Mine fields": L3+, 100k cr, 48h, 1@L3/2@L4/3@L5)."""
    spec = DEFENSE_BUILDINGS["planet_minefield"]
    assert spec["min_citadel_level"] == 3
    assert spec["cost"] == 100000
    assert spec["build_hours"] == 48
    assert spec["max_count"] == {3: 1, 4: 2, 5: 3}


def test_build_succeeds_at_citadel_level_3():
    owner = uuid.uuid4()
    planet = _planet(citadel_level=3, owner_id=owner)
    player = _player()
    player.id = owner

    result = _build(planet, player)

    assert result["success"] is True
    assert result["building_type"] == "planet_minefield"
    queue = planet.active_events["defense_build_queue"]
    assert len(queue) == 1
    assert queue[0]["type"] == "planet_minefield"
    assert player.credits == 1_000_000 - 100000


def test_build_rejected_below_citadel_level_3():
    owner = uuid.uuid4()
    planet = _planet(citadel_level=2, owner_id=owner)
    player = _player()
    player.id = owner

    result = _build(planet, player)

    assert result["success"] is False
    assert "citadel level 3" in result["message"]
    # No credits spent, nothing queued, on a rejected build.
    assert player.credits == 1_000_000
    assert "defense_build_queue" not in planet.active_events


@pytest.mark.parametrize(
    "citadel_level,existing_count,should_succeed",
    [
        (3, 0, True),    # L3: 0/1 -> room for 1
        (3, 1, False),   # L3: 1/1 -> at cap
        (4, 1, True),    # L4: 1/2 -> room for 1 more
        (4, 2, False),   # L4: 2/2 -> at cap
        (5, 2, True),    # L5: 2/3 -> room for 1 more
        (5, 3, False),   # L5: 3/3 -> at cap (max legit buildout)
    ],
)
def test_capacity_ladder_matches_canon_1_2_3(citadel_level, existing_count, should_succeed):
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=citadel_level,
        owner_id=owner,
        defense_buildings={"planet_minefield": existing_count},
    )
    player = _player()
    player.id = owner

    result = _build(planet, player)

    assert result["success"] is should_succeed
    if not should_succeed:
        assert "Maximum" in result["message"]


def test_build_rejected_when_not_the_owner():
    planet = _planet(citadel_level=3, owner_id=uuid.uuid4())
    player = _player()  # a different id -- not the owner

    result = _build(planet, player)

    assert result["success"] is False
    assert "do not own" in result["message"]


# --------------------------------------------------------------------------- #
# Settle: a completed queue entry moves into the operational count.
# --------------------------------------------------------------------------- #

def test_settle_build_queue_increments_operational_planet_minefield_count():
    db = _FakeSession(None, None)
    svc = CitadelService(db)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    planet = SimpleNamespace(
        active_events={
            "defense_buildings": {},
            "defense_build_queue": [
                {"type": "planet_minefield", "started_at": past, "complete_at": past}
            ],
        }
    )

    changed = svc._settle_build_queue(planet, datetime.now(UTC))

    assert changed is True
    assert planet.active_events["defense_buildings"]["planet_minefield"] == 1
    assert planet.active_events["defense_build_queue"] == []
    assert db.flush_count == 1


def test_settle_build_queue_leaves_an_unfinished_build_queued():
    db = _FakeSession(None, None)
    svc = CitadelService(db)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    planet = SimpleNamespace(
        active_events={
            "defense_buildings": {},
            "defense_build_queue": [
                {"type": "planet_minefield", "started_at": "irrelevant", "complete_at": future}
            ],
        }
    )

    changed = svc._settle_build_queue(planet, datetime.now(UTC))

    assert changed is False
    assert planet.active_events["defense_buildings"].get("planet_minefield", 0) == 0
    assert len(planet.active_events["defense_build_queue"]) == 1
