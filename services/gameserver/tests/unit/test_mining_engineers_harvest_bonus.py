"""LEG-2739 — Mining Engineers +30% asteroid harvest ore (mining.md:83)."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.sector import SectorType
from src.services.mining_service import HARVEST_TURN_COST, MiningService
from src.services.profession_service import (
    mining_engineer_ore_multiplier,
    mining_engineer_ore_multiplier_for_region,
    ProfessionType,
)


def _preview_sector(**overrides):
    base = {
        "sector_id": 9,
        "type": SectorType.ASTEROID_FIELD,
        "region_id": uuid.uuid4(),
        "resources": {
            "asteroid_richness": {"richness_tier": 3},
            "depletion_pool": 300,
        },
        "resource_regeneration": 0.5,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_harvest_source_applies_mining_engineer_multiplier():
    source = inspect.getsource(MiningService.resolve_harvest)
    assert "mining_engineer_ore_multiplier_for_region" in source
    assert "mining_mult" in source


def test_preview_yield_applies_mining_engineer_band_multiplier():
    svc = MiningService(MagicMock())
    player_id = uuid.uuid4()
    player = SimpleNamespace(id=player_id, current_sector_id=9)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player_id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 2}},
    )
    sector = _preview_sector()
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    with patch(
        "src.services.mining_service.mining_engineer_ore_multiplier_for_region",
        return_value=1.30,
    ):
        result = svc.preview_yield(ship.id, player.id)

    assert result["success"] is True
    assert result["ore_lo"] == 11  # 9 * 1.30
    assert result["ore_hi"] == 23  # 18 * 1.30
    assert result["mining_engineer_modifier"] == 1.30
    assert result["turns_cost"] == HARVEST_TURN_COST


def test_preview_yield_no_engineers_modifier_is_unity():
    svc = MiningService(MagicMock())
    player_id = uuid.uuid4()
    player = SimpleNamespace(id=player_id, current_sector_id=9)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player_id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 2}},
    )
    sector = _preview_sector()
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    with patch(
        "src.services.mining_service.mining_engineer_ore_multiplier_for_region",
        return_value=1.0,
    ):
        result = svc.preview_yield(ship.id, player.id)

    assert result["success"] is True
    assert result["ore_lo"] == 9
    assert result["ore_hi"] == 18
    assert result["mining_engineer_modifier"] == 1.0


def test_mining_engineer_ore_multiplier_with_active_engineers():
    planet_id = uuid.uuid4()
    db = MagicMock()
    with patch(
        "src.services.profession_service.profession_counts",
        return_value={ProfessionType.MINING_ENGINEERS: 5},
    ):
        assert mining_engineer_ore_multiplier(db, planet_id) == 1.30


def test_mining_engineer_ore_multiplier_for_region_picks_best_planet():
    player_id = uuid.uuid4()
    region_id = uuid.uuid4()
    planet_a = SimpleNamespace(id=uuid.uuid4())
    planet_b = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.query.return_value.join.return_value.join.return_value.filter.return_value.all.return_value = [
        planet_a,
        planet_b,
    ]

    with patch(
        "src.services.profession_service.mining_engineer_ore_multiplier",
        side_effect=[1.0, 1.30],
    ):
        mult = mining_engineer_ore_multiplier_for_region(db, player_id, region_id)

    assert mult == 1.30


def test_mining_engineer_ore_multiplier_for_region_none_region_is_unity():
    assert mining_engineer_ore_multiplier_for_region(MagicMock(), uuid.uuid4(), None) == 1.0
