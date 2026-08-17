"""LEG-424 — mining yield-band preview (mining.md:252). DB-free unit tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.sector import SectorType
from src.services.mining_service import (
    HARVEST_TURN_COST,
    MiningService,
    _YIELD_MATRIX,
    _depletion_yield_modifier,
)


def test_matrix_yield_band_matches_canon_table():
    assert MiningService.matrix_yield_band(1, 0) == _YIELD_MATRIX[1][0] == (2, 4)
    assert MiningService.matrix_yield_band(3, 2) == _YIELD_MATRIX[3][2] == (9, 18)
    assert MiningService.matrix_yield_band(5, 3) == _YIELD_MATRIX[5][3] == (30, 50)


def test_matrix_yield_band_clamps_tier_and_laser():
    assert MiningService.matrix_yield_band(0, 99) == _YIELD_MATRIX[1][3]
    assert MiningService.matrix_yield_band(9, -1) == _YIELD_MATRIX[5][0]


def test_depletion_modifier_fresh_and_heavy():
    assert _depletion_yield_modifier(0.0) == 1.0
    assert _depletion_yield_modifier(0.04) == 1.0
    assert _depletion_yield_modifier(0.10) == 0.75
    assert _depletion_yield_modifier(0.60) == 0.5
    assert _depletion_yield_modifier(0.95) == 0.0


def test_preview_not_asteroid_field():
    svc = MiningService(MagicMock())
    player = SimpleNamespace(id=uuid.uuid4(), current_sector_id=1)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player.id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 1}},
    )
    sector = SimpleNamespace(
        sector_id=1,
        type=SectorType.STANDARD,  # not asteroid
        resources={},
        resource_regeneration=0.5,
    )
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    result = svc.preview_yield(ship.id, player.id)
    assert result["success"] is False
    assert result["reason"] == "not_an_asteroid_field"
    assert result["turns_cost"] == HARVEST_TURN_COST


def test_preview_no_mining_laser():
    svc = MiningService(MagicMock())
    player = SimpleNamespace(id=uuid.uuid4(), current_sector_id=7)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player.id,
        is_destroyed=False,
        equipment_slots={},
    )
    sector = SimpleNamespace(
        sector_id=7,
        type=SectorType.ASTEROID_FIELD,
        resources={"asteroid_richness": {"richness_tier": 2}, "depletion_pool": 200},
        resource_regeneration=0.4,
    )
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    result = svc.preview_yield(ship.id, player.id)
    assert result["success"] is False
    assert result["reason"] == "no_mining_laser"


def test_preview_success_band_and_depletion_modifier():
    svc = MiningService(MagicMock())
    player = SimpleNamespace(id=uuid.uuid4(), current_sector_id=9)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player.id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 2}},
    )
    # tier 3 pool=300; pool remaining 150 → 50% consumed → Moderate 0.75×
    sector = SimpleNamespace(
        sector_id=9,
        type=SectorType.ASTEROID_FIELD,
        resources={
            "asteroid_richness": {"richness_tier": 3},
            "depletion_pool": 150,
        },
        resource_regeneration=0.5,
    )
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    result = svc.preview_yield(ship.id, player.id)
    assert result["success"] is True
    assert result["richness_tier"] == 3
    assert result["laser_level"] == 2
    assert result["ore_lo"] == 9
    assert result["ore_hi"] == 18
    assert result["depletion_modifier"] == 0.75
    assert result["turns_cost"] == 5


def test_yield_preview_route_registered():
    from src.api.routes import mining as mining_routes

    paths = {
        getattr(r, "path", None)
        for r in mining_routes.router.routes
        if "GET" in (getattr(r, "methods", None) or set())
    }
    assert "/mining/yield-preview" in paths
