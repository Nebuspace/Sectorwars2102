"""LEG-2591 — Mining Engineers +30% asteroid harvest bonus (mining.md:83)."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models.mining_harvest import MiningHarvestStatus
from src.models.ship import ShipStatus
from src.services import mining_service as ms
from src.services.mining_service import HARVEST_TURN_COST, MiningService


class _FakeRNG:
    def randint(self, lo, hi):
        return lo

    def random(self):
        return 1.0


class _HarvestQueryStub:
    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row

    def with_for_update(self):
        return self


class _HarvestDBStub:
    def __init__(self, *, harvest_row, sector, player, ship):
        self.harvest_row = harvest_row
        self.sector = sector
        self.player = player
        self.ship = ship

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "MiningHarvest":
            return _HarvestQueryStub(self.harvest_row)
        if name == "Sector":
            return _HarvestQueryStub(self.sector)
        return _HarvestQueryStub(None)

    def flush(self):
        return None


def _resolve_harvest_fixture(monkeypatch, *, me_mult: float):
    player_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    harvest_id = uuid.uuid4()
    region_id = uuid.uuid4()

    harvest_row = SimpleNamespace(
        id=harvest_id,
        status=MiningHarvestStatus.PENDING,
        ship_id=ship_id,
        player_id=player_id,
        sector_id=7,
        richness_tier=3,
        laser_level=0,
        am_claimed=False,
        has_license=False,
        turns_spent=HARVEST_TURN_COST,
        ore_yield=None,
        precious_metals_yield=None,
        quantum_shards_yield=None,
        am_rep_delta=None,
        resolved_at=None,
    )
    sector = SimpleNamespace(
        sector_id=7,
        region_id=region_id,
        resources={"depletion_pool": 300},
        resource_regeneration=0.5,
    )
    player = SimpleNamespace(id=player_id, turns=0)
    ship = SimpleNamespace(
        id=ship_id,
        owner_id=player_id,
        status=ShipStatus.MINING,
        cargo={"used": 0, "contents": {}},
        equipment_slots={"mining_laser": {"level": 0}},
    )

    db = _HarvestDBStub(
        harvest_row=harvest_row, sector=sector, player=player, ship=ship
    )
    svc = MiningService(db)
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._ensure_asteroid_richness = MagicMock()
    svc._sector_is_frontier = MagicMock(return_value=False)
    svc.frontier_coalition_rep_for_harvest = MagicMock(return_value=0)

    monkeypatch.setattr(ms, "_RNG", _FakeRNG())
    monkeypatch.setattr(
        ms,
        "mining_engineer_harvest_multiplier_for_region",
        MagicMock(return_value=me_mult),
    )
    monkeypatch.setattr(ms, "apply_faction_rep_delta", MagicMock())
    monkeypatch.setattr(ms, "effective_cargo_capacity", MagicMock(return_value=100))
    monkeypatch.setattr(ms, "flag_modified", MagicMock())

    return svc, harvest_id


def test_resolve_harvest_applies_mining_engineer_multiplier(monkeypatch):
    svc, harvest_id = _resolve_harvest_fixture(monkeypatch, me_mult=1.30)
    out = svc.resolve_harvest(harvest_id)
    assert out["success"] is True
    # tier-3 L0 base band low=6; depletion fresh → ore = int(6 * 1.0 * 1.30) = 7
    assert out["ore"] == 7


def test_resolve_harvest_without_mining_engineers_unchanged(monkeypatch):
    svc, harvest_id = _resolve_harvest_fixture(monkeypatch, me_mult=1.0)
    out = svc.resolve_harvest(harvest_id)
    assert out["success"] is True
    assert out["ore"] == 6


def test_resolve_harvest_source_wires_mining_engineer_multiplier():
    source = inspect.getsource(MiningService.resolve_harvest)
    assert "mining_engineer_harvest_multiplier_for_region" in source
