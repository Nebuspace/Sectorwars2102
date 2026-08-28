"""LEG-2745: repair_player_ship integration pin for Space Engineer +25% repair
cost reduction (professions.md:32 / player.py:498-501).

Dedupe: test_profession_service.py pins space_engineer_repair_multiplier*
helpers in isolation. This file closes the gap: repair_player_ship
credits_charged reflects baseline / 1.25 when the player owns a planet in
the station sector with SPACE_ENGINEERS assigned.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.api.routes.player import repair_player_ship
from src.models.colonist_profession import ProfessionType
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station
from src.services.profession_service import SPACE_ENGINEER_REPAIR_MULTIPLIER

from tests.unit.test_trading_core_pins import (
    _FakeQuery,
    _FakeSession,
    _neutral_player,
    _neutral_station,
)


def _owned_planet(*, sector_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def _space_engineer_row(planet_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.SPACE_ENGINEERS.value,
        count=10,
    )


def _repair_station() -> Station:
    station = _neutral_station()
    station.services = {"ship_repair": True}
    return station


def _damaged_ship(*, owner_id: uuid.UUID) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        name="Test Fighter",
        type=ShipType.SCOUT_SHIP,
        owner_id=owner_id,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        sector_id=1,
        current_value=10_000,
        purchase_value=10_000,
        maintenance={"condition": 50.0},
        cargo={"capacity": 50, "used": 0, "contents": {}},
        combat={
            "max_hull": 100,
            "max_shields": 100,
            "hull": 50,
            "shields": 50,
        },
    )


def _session_for_repair(
    player: Player,
    station: Station,
    ship: Ship,
    *,
    owned_planets=None,
    professions=None,
) -> _FakeSession:
    return _FakeSession({
        Player: _FakeQuery(first=player),
        Station: _FakeQuery(first=station),
        Ship: _FakeQuery(first=ship),
        Planet: _FakeQuery(all_results=list(owned_planets or [])),
        ColonistProfession: _FakeQuery(all_results=list(professions or [])),
    })


def _baseline_repair_cost(*, current_value: int = 10_000) -> int:
    """50% combined hull+shields deficit on a 100/100 + 100/100 ship."""
    deficit_pct = 50.0
    return int(round(current_value * 0.05 * (deficit_pct / 10.0)))


@pytest.mark.asyncio
class TestSpaceEngineerRepairIntegration:
    """Docked ship repair at a neutral station: compare credits_charged with
    vs without SPACE_ENGINEERS on a player-owned planet in the station sector."""

    async def test_repair_without_space_engineers_baseline(self):
        player = _neutral_player(credits=100_000)
        station = _repair_station()
        player.current_port_id = station.id
        ship = _damaged_ship(owner_id=player.id)
        db = _session_for_repair(player, station, ship)

        result = await repair_player_ship(
            ship_id=str(ship.id),
            player=player,
            db=db,
        )

        expected_cost = _baseline_repair_cost()
        assert expected_cost == 2500
        assert result.credits_charged == expected_cost
        assert player.credits == 100_000 - expected_cost

    async def test_repair_with_space_engineers_applies_125x_cost_reduction(self):
        assert SPACE_ENGINEER_REPAIR_MULTIPLIER == pytest.approx(1.25)
        player = _neutral_player(credits=100_000)
        station = _repair_station()
        player.current_port_id = station.id
        ship = _damaged_ship(owner_id=player.id)
        planet = _owned_planet(sector_id=station.sector_id)
        db = _session_for_repair(
            player,
            station,
            ship,
            owned_planets=[planet],
            professions=[_space_engineer_row(planet.id)],
        )

        result = await repair_player_ship(
            ship_id=str(ship.id),
            player=player,
            db=db,
        )

        baseline_cost = _baseline_repair_cost()
        expected_cost = int(round(baseline_cost / SPACE_ENGINEER_REPAIR_MULTIPLIER))
        assert expected_cost == 2000
        assert result.credits_charged == expected_cost
        assert player.credits == 100_000 - expected_cost
