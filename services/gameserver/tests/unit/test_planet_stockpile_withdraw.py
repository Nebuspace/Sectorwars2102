"""DB-free unit tests for planet stockpile→cargo withdraw + tax skim
(WO-BUILD-PLANET-TAX-RATE-WITHDRAWAL-ROUTE).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship
from src.services.planetary_service import (
    PlanetaryService,
    clamp_tax_rate,
)


def _bind_value(expr) -> Optional[Any]:
    right = getattr(expr, "right", None)
    if right is None:
        return None
    return getattr(right, "value", None)


def _col_key(expr) -> Optional[str]:
    left = getattr(expr, "left", None)
    return getattr(left, "key", None)


class _FakeQuery:
    def __init__(self, session: "_FakeSession", model):
        self._session = session
        self._model = model
        self._filters: List[Any] = []

    def filter(self, *conds):
        self._filters.extend(conds)
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        wanted: Dict[str, Any] = {}
        for f in self._filters:
            key = _col_key(f)
            val = _bind_value(f)
            if key is not None and val is not None:
                wanted[key] = val
        for row in self._session.store.get(self._model, []):
            if all(getattr(row, k, None) == v for k, v in wanted.items()):
                return row
        return None


class _FakeSession:
    def __init__(self, store: Dict[type, List[Any]]):
        self.store = store
        self.flushed = False

    def query(self, model):
        return _FakeQuery(self, model)

    def flush(self):
        self.flushed = True


def _planet(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        fuel_ore=1000,
        organics=500,
        equipment=200,
        tax_rate=0.10,
        citadel_level=1,
        active_events={},
        citadel_safe_credits=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _player(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        is_landed=True,
        current_planet_id=None,
        current_ship_id=uuid.uuid4(),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _ship(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        cargo={"contents": {}, "used": 0, "capacity": 500},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_clamp_tax_rate_bounds():
    assert clamp_tax_rate(None) == 0.0
    assert clamp_tax_rate(0.05) == 0.05
    assert clamp_tax_rate(-1.0) == 0.0
    assert clamp_tax_rate(0.50) == 0.20


def test_owner_withdraw_untaxed():
    planet_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), current_planet_id=planet_id)
    ship = _ship(id=owner.current_ship_id, owner_id=owner.id)
    planet = _planet(
        id=planet_id,
        owner_id=owner.id,
        fuel_ore=100,
        tax_rate=0.20,
    )

    db = _FakeSession({Planet: [planet], Player: [owner], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, owner.id, "fuel_ore", 40
    )

    assert result["success"] is True
    assert result["tax_skimmed"] == 0
    assert result["amount_to_cargo"] == 40
    assert planet.fuel_ore == 60
    assert ship.cargo["contents"]["ore"] == 40
    assert db.flushed is True


def test_teammate_tax_skim_to_safe():
    planet_id = uuid.uuid4()
    team_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), team_id=team_id, is_landed=False)
    mate = _player(
        id=uuid.uuid4(),
        team_id=team_id,
        current_planet_id=planet_id,
    )
    ship = _ship(id=mate.current_ship_id, owner_id=mate.id)
    planet = _planet(
        id=planet_id,
        owner_id=owner.id,
        fuel_ore=100,
        tax_rate=0.10,
        citadel_level=1,
        active_events={},
    )

    db = _FakeSession({Planet: [planet], Player: [owner, mate], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, mate.id, "fuel_ore", 100
    )

    assert result["success"] is True
    assert result["tax_skimmed"] == 10
    assert result["amount_to_cargo"] == 90
    assert planet.fuel_ore == 0
    assert ship.cargo["contents"]["ore"] == 90
    assert planet.active_events["safe_commodities"]["fuel_ore"] == 10


def test_non_team_rejected():
    planet_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), team_id=uuid.uuid4())
    stranger = _player(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        current_planet_id=planet_id,
    )
    ship = _ship(id=stranger.current_ship_id, owner_id=stranger.id)
    planet = _planet(id=planet_id, owner_id=owner.id, fuel_ore=50)

    db = _FakeSession({Planet: [planet], Player: [owner, stranger], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, stranger.id, "fuel_ore", 10
    )
    assert result["success"] is False
    assert "team" in result["message"].lower() or "own" in result["message"].lower()
    assert planet.fuel_ore == 50


def test_not_landed_rejected():
    planet_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), is_landed=False, current_planet_id=None)
    ship = _ship(id=owner.current_ship_id, owner_id=owner.id)
    planet = _planet(id=planet_id, owner_id=owner.id, fuel_ore=50)

    db = _FakeSession({Planet: [planet], Player: [owner], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, owner.id, "fuel_ore", 10
    )
    assert result["success"] is False
    assert "landed" in result["message"].lower()


def test_tax_without_citadel_fails_closed():
    planet_id = uuid.uuid4()
    team_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), team_id=team_id)
    mate = _player(id=uuid.uuid4(), team_id=team_id, current_planet_id=planet_id)
    ship = _ship(id=mate.current_ship_id, owner_id=mate.id)
    planet = _planet(
        id=planet_id,
        owner_id=owner.id,
        fuel_ore=100,
        tax_rate=0.10,
        citadel_level=0,
    )

    db = _FakeSession({Planet: [planet], Player: [owner, mate], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, mate.id, "fuel_ore", 50
    )

    assert result["success"] is False
    assert "citadel" in result["message"].lower()
    assert planet.fuel_ore == 100


def test_cargo_full_fails_before_mutate():
    planet_id = uuid.uuid4()
    owner = _player(id=uuid.uuid4(), current_planet_id=planet_id)
    ship = _ship(
        id=owner.current_ship_id,
        owner_id=owner.id,
        cargo={"contents": {"ore": 100}, "used": 100, "capacity": 100},
    )
    planet = _planet(id=planet_id, owner_id=owner.id, fuel_ore=50, tax_rate=0.0)

    db = _FakeSession({Planet: [planet], Player: [owner], Ship: [ship]})
    result = PlanetaryService(db).withdraw_stockpile_to_cargo(
        planet_id, owner.id, "fuel_ore", 10
    )

    assert result["success"] is False
    assert "cargo" in result["message"].lower()
    assert planet.fuel_ore == 50
