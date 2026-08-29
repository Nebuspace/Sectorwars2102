"""LEG-2732 — Mining Engineers +30% ore at asteroid harvest resolve/preview."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.colonist_profession import ProfessionType
from src.models.sector import SectorType
from src.services.mining_service import MiningService
from src.services.profession_service import MINING_ENGINEER_ORE_MULTIPLIER


def test_preview_yield_applies_mining_engineer_multiplier_to_band():
    svc = MiningService(MagicMock())
    player = SimpleNamespace(id=uuid.uuid4(), current_sector_id=9)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player.id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 2}},
    )
    region_id = uuid.uuid4()
    sector = SimpleNamespace(
        sector_id=9,
        type=SectorType.ASTEROID_FIELD,
        region_id=region_id,
        resources={
            "asteroid_richness": {"richness_tier": 3},
            "depletion_pool": 300,
        },
        resource_regeneration=0.5,
    )
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    with patch(
        "src.services.mining_service.mining_engineer_ore_multiplier_for_region",
        return_value=MINING_ENGINEER_ORE_MULTIPLIER,
    ) as region_mult:
        result = svc.preview_yield(ship.id, player.id)

    region_mult.assert_called_once_with(svc.db, player.id, region_id)
    assert result["success"] is True
    assert result["ore_lo"] == int(9 * MINING_ENGINEER_ORE_MULTIPLIER)
    assert result["ore_hi"] == int(18 * MINING_ENGINEER_ORE_MULTIPLIER)


def test_preview_yield_no_engineers_keeps_canon_band():
    svc = MiningService(MagicMock())
    player = SimpleNamespace(id=uuid.uuid4(), current_sector_id=9)
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=player.id,
        is_destroyed=False,
        equipment_slots={"mining_laser": {"level": 2}},
    )
    sector = SimpleNamespace(
        sector_id=9,
        type=SectorType.ASTEROID_FIELD,
        region_id=uuid.uuid4(),
        resources={
            "asteroid_richness": {"richness_tier": 3},
            "depletion_pool": 300,
        },
        resource_regeneration=0.5,
    )
    svc._lock_player_and_ship = MagicMock(return_value=(player, ship, None))
    svc._resolve_current_sector = MagicMock(return_value=sector)

    with patch(
        "src.services.mining_service.mining_engineer_ore_multiplier_for_region",
        return_value=1.0,
    ):
        result = svc.preview_yield(ship.id, player.id)

    assert result["ore_lo"] == 9
    assert result["ore_hi"] == 18


def test_resolve_harvest_source_applies_mining_engineer_multiplier():
    from src.services.mining_service import MiningService

    source = inspect.getsource(MiningService.resolve_harvest)
    assert "mining_engineer_ore_multiplier_for_region" in source
    assert "mining_mult" in source
    assert "base_ore * efficiency * depletion_mod * mining_mult" in source.replace(
        "\n", " "
    )


def test_mining_engineer_multiplier_magnitude_is_canon_130():
    assert MINING_ENGINEER_ORE_MULTIPLIER == pytest.approx(1.30)
