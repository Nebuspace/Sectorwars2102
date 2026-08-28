"""LEG-2744: sell_resource integration pin for Trade Specialist +25% credit
multiplier on station ore sells (mining.md:84 / professions.md L57).

Dedupe: test_profession_service.py pins trade_specialist_credit_multiplier*
helpers in isolation; test_trading_core_pins.py pins sell_resource math with
Planet query returning [] (multiplier stays 1.0). This file closes the gap:
sell_resource net_earnings and player.credits reflect 1.25x when the player
owns a planet in the station's sector with TRADE_SPECIALISTS assigned.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.trading import TradeRequest, sell_resource
from src.models.colonist_profession import ColonistProfession, ProfessionType
from src.models.market_transaction import MarketPrice
from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship
from src.models.station import Station
from src.services.profession_service import TRADE_SPECIALIST_CREDIT_MULTIPLIER

from tests.unit.test_trading_core_pins import (
    _FakeQuery,
    _FakeSession,
    _market_price,
    _neutral_player,
    _neutral_station,
    _ship,
)


def _owned_planet(*, sector_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def _trade_specialist_row(planet_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.TRADE_SPECIALISTS.value,
        count=10,
    )


def _session_for_sell(
    player: Player,
    station: Station,
    ship: Ship,
    market_price: MarketPrice,
    *,
    owned_planets=None,
    professions=None,
) -> _FakeSession:
    """Fake session for sell_resource with optional owned-planet profession rows."""
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(seq=[player, None]),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
        Planet: _FakeQuery(all_results=list(owned_planets or [])),
        ColonistProfession: _FakeQuery(all_results=list(professions or [])),
    })


@pytest.fixture(autouse=True)
def _quiet_websocket_pushes():
    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.asyncio
class TestTradeSpecialistSellIntegration:
    """Ore sell at a neutral station: compare net credits with vs without
    TRADE_SPECIALISTS on a player-owned planet in the station sector."""

    async def test_sell_ore_without_trade_specialists_baseline(self):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for_sell(player, station, ship, mp)

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db,
            current_user=None,
            current_player=player,
        )

        assert result["transaction"]["net_earnings"] == 196
        assert player.credits == 1_000 + 196

    async def test_sell_ore_with_trade_specialists_applies_125x_multiplier(self):
        assert TRADE_SPECIALIST_CREDIT_MULTIPLIER == pytest.approx(1.25)
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        planet = _owned_planet(sector_id=station.sector_id)
        db = _session_for_sell(
            player,
            station,
            ship,
            mp,
            owned_planets=[planet],
            professions=[_trade_specialist_row(planet.id)],
        )

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db,
            current_user=None,
            current_player=player,
        )

        baseline_net = 196
        expected_net = int(round(baseline_net * TRADE_SPECIALIST_CREDIT_MULTIPLIER))
        assert expected_net == 245
        assert result["transaction"]["net_earnings"] == expected_net
        assert player.credits == 1_000 + expected_net
