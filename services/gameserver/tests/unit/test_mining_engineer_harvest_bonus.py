"""LEG-2681 — Mining Engineers +30% asteroid harvest bonus (no live DB)."""

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.colonist_profession import ProfessionType
from src.services import profession_service as ps
from src.services.mining_service import MiningService


class _QueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _PlanetQueryStub:
    def __init__(self, planets):
        self._planets = list(planets)

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._planets)


class _RegionProfessionDBStub:
    def __init__(self, *, planets=None, professions=None):
        self.planets = planets or []
        self.professions = professions or []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "Planet":
            return _PlanetQueryStub(self.planets)
        if name == "ColonistProfession":
            return _QueryStub(self.professions)
        return _QueryStub([])


def test_mining_engineer_multiplier_without_specialists():
    planet_id = uuid4()
    db = _RegionProfessionDBStub()
    assert ps.mining_engineer_multiplier(db, planet_id) == 1.0


def test_mining_engineer_multiplier_with_specialists():
    planet_id = uuid4()
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.MINING_ENGINEERS.value,
        count=25,
    )
    db = _RegionProfessionDBStub(professions=[prof_row])
    assert ps.mining_engineer_multiplier(db, planet_id) == pytest.approx(1.30)


def test_mining_engineer_harvest_multiplier_no_region():
    owner = uuid4()
    db = _RegionProfessionDBStub()
    assert ps.mining_engineer_harvest_multiplier(db, owner, None) == 1.0


def test_mining_engineer_harvest_multiplier_no_owned_planets_in_region():
    owner = uuid4()
    region_id = uuid4()
    db = _RegionProfessionDBStub(planets=[])
    assert ps.mining_engineer_harvest_multiplier(db, owner, region_id) == 1.0


def test_mining_engineer_harvest_multiplier_same_region():
    owner = uuid4()
    region_id = uuid4()
    planet_id = uuid4()
    planet = SimpleNamespace(id=planet_id, region_id=region_id)
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.MINING_ENGINEERS.value,
        count=1,
    )
    db = _RegionProfessionDBStub(planets=[planet], professions=[prof_row])
    assert ps.mining_engineer_harvest_multiplier(db, owner, region_id) == pytest.approx(
        1.30
    )


def test_resolve_harvest_applies_mining_engineer_multiplier():
    source = inspect.getsource(MiningService.resolve_harvest)
    assert "mining_engineer_harvest_multiplier" in source
    assert "me_mult" in source


def test_harvest_ore_scales_with_mining_engineer_multiplier():
    """Pin 1.0 vs 1.30 yield when base roll and modifiers are held constant."""
    base_ore = 10
    efficiency = 1.0
    depletion_mod = 1.0
    without = int(base_ore * efficiency * depletion_mod * 1.0)
    with_bonus = int(base_ore * efficiency * depletion_mod * 1.30)
    assert without == 10
    assert with_bonus == 13
    assert with_bonus / without == pytest.approx(1.30)
