"""LEG-2684 — Structural Engineers −20% CRT grid building placement credit cost."""

import inspect
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.api.routes import planet_grid as grid_routes
from src.models.colonist_profession import ColonistProfession, ProfessionType
from src.models.planet import Planet
from src.models.player import Player
from src.services import profession_service as ps


class _QueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def populate_existing(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _PlaceDBStub:
    def __init__(self, planet, player, professions=None):
        self._planet = planet
        self._player = player
        self.professions = professions or []
        self.committed = False

    def query(self, model):
        if model is Planet:
            return _QueryStub([self._planet])
        if model is Player:
            return _QueryStub([self._player])
        if model is ColonistProfession:
            return _QueryStub(self.professions)
        name = getattr(model, "__name__", str(model))
        if name == "ColonistProfession":
            return _QueryStub(self.professions)
        raise AssertionError(f"unexpected query model: {model}")

    def commit(self):
        self.committed = True


def _cleared_grid_structures():
  return {
      "version": 1,
      "grid": {"cols": 3, "rows": 3},
      "plots": [
          {"x": 0, "y": 0, "cleared": True, "surveyed": True},
          {"x": 1, "y": 0, "cleared": True, "surveyed": True},
          {"x": 2, "y": 0, "cleared": True, "surveyed": True},
          {"x": 0, "y": 1, "cleared": True, "surveyed": True},
          {"x": 1, "y": 1, "cleared": True, "surveyed": True},
          {"x": 2, "y": 1, "cleared": True, "surveyed": True},
          {"x": 0, "y": 2, "cleared": True, "surveyed": True},
          {"x": 1, "y": 2, "cleared": True, "surveyed": True},
          {"x": 2, "y": 2, "cleared": True, "surveyed": True},
      ],
      "buildings": [],
  }


def test_structural_engineer_multiplier_without_specialists():
    planet_id = uuid4()
    db = _PlaceDBStub(None, None)
    assert ps.structural_engineer_cost_multiplier(db, planet_id) == 1.0


def test_structural_engineer_multiplier_with_specialists():
    planet_id = uuid4()
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.STRUCTURAL_ENGINEERS.value,
        count=10,
    )
    db = _PlaceDBStub(None, None, professions=[prof_row])
    assert ps.structural_engineer_cost_multiplier(db, planet_id) == pytest.approx(0.80)


def test_place_building_applies_structural_engineer_multiplier():
    source = inspect.getsource(grid_routes.place_building)
    assert "structural_engineer_cost_multiplier" in source


def test_farm_l1_catalog_discount_math_pin():
    catalog_cost = 30000
    assert int(round(catalog_cost * 0.80)) == 24000


@pytest.mark.asyncio
@patch("src.api.routes.planet_grid.flag_modified")
async def test_place_building_charges_discounted_credits_with_structural_engineers(
    mock_flag_modified,
):
    owner_id = uuid4()
    planet_id = uuid4()
    planet = SimpleNamespace(
        id=planet_id,
        owner_id=owner_id,
        size=5,
        structures=_cleared_grid_structures(),
        active_events={},
        fuel_ore=0,
        organics=0,
        equipment=0,
    )
    player = SimpleNamespace(
        id=owner_id,
        credits=1_000_000,
        research_ledger={"rp": 0, "unlocked": ["free_root"]},
    )
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.STRUCTURAL_ENGINEERS.value,
        count=5,
    )
    db = _PlaceDBStub(planet, player, professions=[prof_row])

    body = grid_routes.GridPlaceRequest(kind="FARM", x=0, y=0, level=1)
    result = await grid_routes.place_building(
        str(planet_id), body, player=player, db=db
    )

    assert result["success"] is True
    assert player.credits == 1_000_000 - 24000
    assert result["remaining_credits"] == 1_000_000 - 24000
    assert db.committed


@pytest.mark.asyncio
@patch("src.api.routes.planet_grid.flag_modified")
async def test_place_building_charges_full_catalog_without_structural_engineers(
    mock_flag_modified,
):
    owner_id = uuid4()
    planet_id = uuid4()
    planet = SimpleNamespace(
        id=planet_id,
        owner_id=owner_id,
        size=5,
        structures=_cleared_grid_structures(),
        active_events={},
        fuel_ore=0,
        organics=0,
        equipment=0,
    )
    player = SimpleNamespace(
        id=owner_id,
        credits=1_000_000,
        research_ledger={"rp": 0, "unlocked": ["free_root"]},
    )
    db = _PlaceDBStub(planet, player, professions=[])

    body = grid_routes.GridPlaceRequest(kind="FARM", x=0, y=0, level=1)
    result = await grid_routes.place_building(
        str(planet_id), body, player=player, db=db
    )

    assert result["success"] is True
    assert player.credits == 1_000_000 - 30000
    assert result["remaining_credits"] == 1_000_000 - 30000
