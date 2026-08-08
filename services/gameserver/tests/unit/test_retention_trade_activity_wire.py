"""DB-free pins for WO-WIRE-RETENTION-TRADE-ACTIVITY.

``PlayerActivityService.track_activity`` had zero production callers
(``retention_service.py``'s own "STILL DORMANT" note) -- the
``economic_loss_streak`` at-risk signal and the Redis session
``trades_count``/``trade_volume`` counters never populated for any trade,
because the trading routes never invoked it. This wires a best-effort call
into both the buy and sell completion paths, mirroring the existing
post-trade hook pattern (rank points, ARIA memory/observation) already
proven in test_aria_trade_hooks.py.

Harness: reuses that file's proven DB-free _FakeSession/_FakeQuery/
_neutral_player/_neutral_station/_ship/_market_price convention for calling
the REAL route coroutines directly (no cross-test-file import -- each
trading.py test file keeps its own self-contained harness, per that file's
own stated precedent).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.trading import TradeRequest, buy_resource, sell_resource
from src.models.market_transaction import MarketPrice
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType
from src.services.player_activity_service import ActivityEventType


class _FakeQuery:
    def __init__(self, *, first: Any = None, seq=None) -> None:
        self._first = first
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first


class _FakeSession:
    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.added: List[Any] = []

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _neutral_player(*, credits: int, turns: int = 50) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credits=credits,
        turns=turns,
        current_sector_id=7,
        current_ship_id=uuid.uuid4(),
        is_docked=True,
        military_rank="Recruit",
        reputation_tier="Neutral",
        personal_reputation=0,
        settings={},
        team_id=None,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
    )


def _neutral_station() -> Station:
    return Station(
        id=uuid.uuid4(),
        name="Neutral Station",
        sector_id=7,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities={},
        faction_affiliation=None,
        region_id=None,
        owner_id=None,
        tax_rate=None,
    )


def _ship(*, capacity=100, used=0, contents=None) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        name="Test Hauler",
        type=ShipType.CARGO_HAULER,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        sector_id=7,
        maintenance={"condition": 80.0},
        cargo={"capacity": capacity, "used": used, "contents": dict(contents or {})},
        combat={},
    )


def _market_price(station_id, *, buy_price, sell_price, quantity, commodity="ore") -> MarketPrice:
    return MarketPrice(
        id=uuid.uuid4(), station_id=station_id, commodity=commodity,
        buy_price=buy_price, sell_price=sell_price, quantity=quantity,
        price_trend=0.0,
    )


def _session_for(player: Player, station: Station, ship: Ship, market_price: MarketPrice) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(seq=[player, None]),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
    })


class _FakeActivityService:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.raises: Optional[Exception] = None

    async def track_activity(self, player_id, event_type, details=None):
        self.calls.append({"player_id": player_id, "event_type": event_type, "details": details})
        if self.raises is not None:
            raise self.raises


@pytest.fixture
def fake_activity():
    fake = _FakeActivityService()
    with patch(
        "src.services.player_activity_service.get_player_activity_service",
        new=AsyncMock(return_value=fake),
    ):
        yield fake


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    """Suppress unrelated post-trade surfaces (real-time pushes, ARIA) --
    same suppression convention as test_aria_trade_hooks.py / test_trading_
    core_pins.py; this lane owns only the activity-tracking wire."""
    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)), \
         patch(
             "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
             return_value=None,
         ):
        yield


@pytest.mark.asyncio
class TestRetentionTradeActivityWireBuy:
    async def test_buy_records_exactly_one_trade_buy_activity_event(self, fake_activity):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp)

        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert len(fake_activity.calls) == 1
        call = fake_activity.calls[0]
        assert call["player_id"] == str(player.id)
        assert call["event_type"] == ActivityEventType.TRADE_BUY
        assert call["details"]["total_value"] == 300  # 10 * 30 (buy charges sell_price)
        assert call["details"]["commodity"] == "ore"
        assert call["details"]["quantity"] == 10
        assert call["details"]["station_id"] == str(station.id)

    async def test_activity_tracking_raise_never_fails_the_buy(self, fake_activity):
        fake_activity.raises = RuntimeError("redis unreachable")
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp)

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert result is not None
        assert len(fake_activity.calls) == 1  # the call was attempted before it raised


@pytest.mark.asyncio
class TestRetentionTradeActivityWireSell:
    async def test_sell_records_exactly_one_trade_sell_activity_event(self, fake_activity):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp)

        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert len(fake_activity.calls) == 1
        call = fake_activity.calls[0]
        assert call["player_id"] == str(player.id)
        assert call["event_type"] == ActivityEventType.TRADE_SELL
        assert call["details"]["total_value"] == 200  # 10 * 20 (sell pays buy_price)
        assert call["details"]["commodity"] == "ore"
        assert call["details"]["quantity"] == 10
        assert call["details"]["station_id"] == str(station.id)

    async def test_activity_tracking_raise_never_fails_the_sell(self, fake_activity):
        fake_activity.raises = RuntimeError("redis unreachable")
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp)

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert result is not None
        assert len(fake_activity.calls) == 1
