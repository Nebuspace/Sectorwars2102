"""Regression: station tariff applies to owner and co-owners (port-ownership.md:227).

Canon: the owner cannot exempt themselves; team and syndicate co-owners pay
station.tax_rate on every buy/sell. Unowned stations levy none.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import pytest

from src.api.routes.trading import (
    TradeQuoteRequest,
    TradeRequest,
    buy_resource,
    compute_buy_totals,
    get_trade_quote,
    sell_resource,
)
from src.models.market_transaction import MarketPrice
from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType


class _FakeQuery:
    def __init__(self, *, first: Any = None, all_results=None) -> None:
        self._first = first
        self._all = list(all_results) if all_results is not None else []

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def join(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> list:
        return self._all


class _FakeSession:
    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.added = []
        self.commit_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_calls += 1

    def flush(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _player(
    *,
    credits: int = 10_000,
    team_id: Optional[uuid.UUID] = None,
) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credits=credits,
        turns=50,
        current_sector_id=1,
        current_ship_id=uuid.uuid4(),
        is_docked=True,
        military_rank="Recruit",
        reputation_tier="Neutral",
        personal_reputation=0,
        settings={},
        team_id=team_id,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
    )


def _station(
    *,
    owner_id: Optional[uuid.UUID],
    tax_rate: float = 0.10,
    ownership: Optional[dict] = None,
) -> Station:
    return Station(
        id=uuid.uuid4(),
        name="Owned Station",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities={},
        faction_affiliation=None,
        region_id=None,
        owner_id=owner_id,
        tax_rate=tax_rate,
        price_modifiers={},
        ownership=ownership or {},
    )


def _ship(*, capacity=100, used=0, contents=None) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        name="Test Hauler",
        type=ShipType.CARGO_HAULER,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        sector_id=1,
        maintenance={"condition": 80.0},
        cargo={"capacity": capacity, "used": used, "contents": dict(contents or {})},
        combat={},
    )


def _market_price(station_id, *, buy_price=20, sell_price=30, quantity=500) -> MarketPrice:
    return MarketPrice(
        id=uuid.uuid4(),
        station_id=station_id,
        commodity="ore",
        buy_price=buy_price,
        sell_price=sell_price,
        quantity=quantity,
    )


def _quote_session(station: Station, market_price: MarketPrice) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        MarketPrice: _FakeQuery(first=market_price),
    })


def _trade_session(
    player: Player,
    station: Station,
    ship: Ship,
    market_price: MarketPrice,
) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(first=player),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
        Planet: _FakeQuery(all_results=[]),
    })


@pytest.fixture(autouse=True)
def _quiet_websocket_pushes():
    from unittest.mock import AsyncMock, patch

    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.parametrize("action", ["buy", "sell"])
@pytest.mark.asyncio
async def test_solo_owner_pays_station_tariff(action: str):
    """Owner trading at their own station is taxed — no self-exemption."""
    owner = _player(credits=10_000)
    station = _station(owner_id=owner.id, tax_rate=0.10)
    ship = _ship(capacity=100, used=10, contents={"ore": 10})
    mp = _market_price(station.id)

    quote = await get_trade_quote(
        quote_request=TradeQuoteRequest(
            station_id=str(station.id),
            resource_type="ore",
            quantity=10,
            action=action,
        ),
        db=_quote_session(station, mp),
        current_user=None,
        current_player=owner,
    )
    assert quote["tax_rate"] == 0.10
    assert quote["tax"] > 0

    if action == "buy":
        result = await buy_resource(
            trade_request=TradeRequest(
                station_id=str(station.id), resource_type="ore", quantity=10,
            ),
            db=_trade_session(owner, station, ship, mp),
            current_user=None,
            current_player=owner,
        )
        assert result["transaction"]["tax"] == quote["tax"]
    else:
        result = await sell_resource(
            trade_request=TradeRequest(
                station_id=str(station.id), resource_type="ore", quantity=10,
            ),
            db=_trade_session(owner, station, ship, mp),
            current_user=None,
            current_player=owner,
        )
        assert result["transaction"]["tax"] == quote["tax"]


@pytest.mark.asyncio
async def test_team_co_owner_pays_station_tariff_on_buy():
    """Same-team member pays tariff even when not the recorded owner_id."""
    team_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    teammate = _player(credits=10_000, team_id=team_id)
    station = _station(owner_id=owner_id, tax_rate=0.12)
    mp = _market_price(station.id)

    quote = await get_trade_quote(
        quote_request=TradeQuoteRequest(
            station_id=str(station.id),
            resource_type="ore",
            quantity=5,
            action="buy",
        ),
        db=_quote_session(station, mp),
        current_user=None,
        current_player=teammate,
    )
    assert quote["tax_rate"] == 0.12
    assert quote["tax"] == compute_buy_totals(30, 5, 0.12)["tax_amount"]


@pytest.mark.asyncio
async def test_syndicate_stake_holder_pays_station_tariff_on_sell():
    """Syndicate co-owner (share ledger, not owner_id) pays tariff on sell."""
    primary_id = uuid.uuid4()
    co_owner = _player(credits=1_000)
    station = _station(
        owner_id=primary_id,
        tax_rate=0.15,
        ownership={
            "co_ownership_mode": "syndicate",
            "co_ownership_shares": [
                {"player_id": str(primary_id), "pct": 70},
                {"player_id": str(co_owner.id), "pct": 30},
            ],
        },
    )
    ship = _ship(capacity=100, used=8, contents={"ore": 8})
    mp = _market_price(station.id)

    quote = await get_trade_quote(
        quote_request=TradeQuoteRequest(
            station_id=str(station.id),
            resource_type="ore",
            quantity=8,
            action="sell",
        ),
        db=_quote_session(station, mp),
        current_user=None,
        current_player=co_owner,
    )
    assert quote["tax_rate"] == 0.15
    assert quote["tax"] > 0

    result = await sell_resource(
        trade_request=TradeRequest(
            station_id=str(station.id), resource_type="ore", quantity=8,
        ),
        db=_trade_session(co_owner, station, ship, mp),
        current_user=None,
        current_player=co_owner,
    )
    assert result["transaction"]["tax"] == quote["tax"]


@pytest.mark.asyncio
async def test_unowned_station_levies_no_tariff():
    station = _station(owner_id=None, tax_rate=0.25)
    player = _player(credits=10_000)
    mp = _market_price(station.id)

    quote = await get_trade_quote(
        quote_request=TradeQuoteRequest(
            station_id=str(station.id),
            resource_type="ore",
            quantity=10,
            action="buy",
        ),
        db=_quote_session(station, mp),
        current_user=None,
        current_player=player,
    )
    assert quote["tax_rate"] == 0.0
    assert quote["tax"] == 0
