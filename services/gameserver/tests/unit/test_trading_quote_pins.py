"""DB-free regression pins for POST /api/v1/trading/quote (WO-API-B1): the
server-authoritative, read-only preview endpoint the client renders instead
of recomputing unit_price/tax/total itself.

The core claim under test is quote == charge: calling get_trade_quote and
then immediately calling buy_resource/sell_resource with the IDENTICAL
station/commodity/quantity/action must produce a committed transaction whose
unit_price/subtotal/tax/total fields are byte-identical to what the quote
predicted. This is deliberately proven by comparing two REAL route-coroutine
invocations against each other (not by re-deriving the arithmetic locally),
so it actually exercises that /quote resolves base_price, the haggle peek,
and the tax rate the exact same way the commit path does -- a passing test
here is only meaningful because both routes are exercised end to end.

Dedupe: test_trading_core_pins.py already pins buy_resource/sell_resource's
own price math (dynamic-price spread, band clamp, rank/first-login bonus).
This file does not re-pin that -- it reuses the SAME neutral/bonus fixture
conventions (mirrored, not imported -- each trading test file keeps its own
private fake-session helpers per this suite's established convention) and
adds only the new /quote surface plus the shared compute_buy_totals /
compute_sell_totals extraction.

Fake-session pattern mirrors test_trading_core_pins.py's _FakeQuery/
_FakeSession (itself generalized from test_warp_gate_toll.py): filter() /
populate_existing() / with_for_update() are no-ops, and a query for a model
with no registered spec raises AssertionError -- this doubles as a
structural proof that /quote never touches Player or Ship rows (no lock,
no re-query -- see TestQuoteIsReadOnly, whose FakeSession registers ONLY
Station + MarketPrice)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import pytest

from src.api.routes.trading import (
    TradeQuoteRequest,
    TradeRequest,
    buy_resource,
    compute_buy_totals,
    compute_sell_totals,
    get_trade_quote,
    sell_resource,
)
from src.models.market_transaction import MarketPrice
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType


class _FakeQuery:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first


class _FakeSession:
    """Maps a model class to its fake query. A query for an unregistered
    model raises AssertionError -- see module docstring."""

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


def _neutral_player(*, credits: int, settings: Optional[Dict[str, Any]] = None) -> Player:
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
        settings=settings if settings is not None else {},
        team_id=None,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
    )


def _neutral_station() -> Station:
    """Untaxed (owner_id=None -> tax_rate resolves to 0.0 in the route
    regardless of the tax_rate column) -- every optional price modifier
    resolves to neutral with zero extra queries. commodities={} short-
    circuits _ensure_market_prices before it ever touches MarketPrice."""
    return Station(
        id=uuid.uuid4(),
        name="Neutral Station",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities={},
        faction_affiliation=None,
        region_id=None,
        owner_id=None,
        tax_rate=None,
        price_modifiers={},
    )


def _taxed_station(*, tax_rate: float) -> Station:
    """Real nonzero tax_rate via a non-None owner_id DIFFERENT from the
    trading player's id, with the player's team_id left None -- E-F1's
    _buyer_is_station_owner_or_team short-circuits to False without any
    Player re-query (see trading_service.py), so this stays DB-free."""
    return Station(
        id=uuid.uuid4(),
        name="Taxed Station",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities={},
        faction_affiliation=None,
        region_id=None,
        owner_id=uuid.uuid4(),
        tax_rate=tax_rate,
        price_modifiers={},
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


def _market_price(station_id, *, buy_price, sell_price, quantity) -> MarketPrice:
    return MarketPrice(
        id=uuid.uuid4(), station_id=station_id, commodity="ore",
        buy_price=buy_price, sell_price=sell_price, quantity=quantity,
    )


def _quote_session(station: Station, market_price: MarketPrice) -> _FakeSession:
    """Registers ONLY Station + MarketPrice -- /quote must never query
    Player or Ship (no lock, no state mutation)."""
    return _FakeSession({
        Station: _FakeQuery(first=station),
        MarketPrice: _FakeQuery(first=market_price),
    })


def _trade_session(player: Player, station: Station, ship: Ship, market_price: MarketPrice) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(first=player),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
    })


@pytest.fixture(autouse=True)
def _quiet_websocket_pushes():
    from unittest.mock import AsyncMock, patch
    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)):
        yield


class TestSharedTotalsHelpers:
    """compute_buy_totals/compute_sell_totals are the callables BOTH the
    commit path and /quote invoke -- pin their arithmetic directly (int()
    truncation, buy adds / sell withholds tax) before trusting the
    route-level comparisons below."""

    def test_buy_totals_truncates_tax_with_int(self):
        totals = compute_buy_totals(30, 7, 0.07)
        # 30*7=210; 210*0.07=14.7 -> int() truncates to 14, NOT rounds to 15.
        assert totals == {"total_cost": 210, "tax_amount": 14, "total_with_tax": 224}

    def test_sell_totals_withholds_tax_from_gross(self):
        totals = compute_sell_totals(20, 6, 0.10)
        assert totals == {"total_earnings": 120, "tax_amount": 12, "net_earnings": 108}


@pytest.mark.asyncio
class TestQuoteEqualsChargeBuy:
    """Each test calls get_trade_quote, THEN buy_resource, against the same
    station/player/ship/market_price objects, and asserts the quote's
    numbers equal the committed transaction's numbers exactly."""

    async def test_neutral_untaxed_quote_matches_charge(self):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=10, action="buy",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 30
        assert quote["subtotal"] == 300
        assert quote["tax"] == 0
        assert quote["total"] == 300

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_cost"] == quote["subtotal"]
        assert result["transaction"]["tax"] == quote["tax"]
        assert result["transaction"]["total_with_tax"] == quote["total"]

    async def test_taxed_station_truncation_tier_quote_matches_charge(self):
        """7% tax on a 210-cr subtotal (30/unit x 7) truncates to 14, not 15
        -- the exact edge case compute_buy_totals's own unit test pins;
        proven here end to end through both routes."""
        player = _neutral_player(credits=10_000)
        station = _taxed_station(tax_rate=0.07)
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=7, action="buy",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 30
        assert quote["subtotal"] == 210
        assert quote["tax"] == 14
        assert quote["total"] == 224

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=7),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_cost"] == quote["subtotal"]
        assert result["transaction"]["tax"] == quote["tax"]
        assert result["transaction"]["total_with_tax"] == quote["total"]
        assert player.credits == 10_000 - 224

    async def test_band_clamp_ceiling_quote_matches_charge(self):
        """Posted sell_price (999) is far above ore's canon max (45) --
        compute_effective_unit_price's FINAL clamp must land both the quote
        and the charge on 45, not 999."""
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=999, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=3, action="buy",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 45
        assert quote["total"] == 135

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=3),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_with_tax"] == quote["total"]


@pytest.mark.asyncio
class TestQuoteEqualsChargeSell:

    async def test_neutral_untaxed_quote_matches_charge(self):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=10, action="sell",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 20
        assert quote["subtotal"] == 200
        assert quote["tax"] == 0
        assert quote["total"] == 200

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_earnings"] == quote["subtotal"]
        assert result["transaction"]["tax"] == quote["tax"]
        assert result["transaction"]["net_earnings"] == quote["total"]

    async def test_taxed_station_truncation_tier_quote_matches_charge(self):
        player = _neutral_player(credits=1_000)
        station = _taxed_station(tax_rate=0.10)
        ship = _ship(capacity=100, used=6, contents={"ore": 6})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=6, action="sell",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        # 20*6=120; 10% tax = 12 (exact, no truncation edge here -- the
        # truncation tier is pinned on the buy side above); net=108.
        assert quote == {
            "station_id": str(station.id),
            "resource_type": "ore",
            "quantity": 6,
            "action": "sell",
            "unit_price": 20,
            "subtotal": 120,
            "tax_rate": 0.10,
            "tax": 12,
            "total": 108,
        }

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=6),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_earnings"] == quote["subtotal"]
        assert result["transaction"]["tax"] == quote["tax"]
        assert result["transaction"]["net_earnings"] == quote["total"]
        assert player.credits == 1_000 + 108

    async def test_band_clamp_floor_quote_matches_charge(self):
        """Posted buy_price (5) is far below ore's canon min (15) -- the
        FINAL clamp must land both the quote and the payout on 15."""
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=6, contents={"ore": 6})
        mp = _market_price(station.id, buy_price=5, sell_price=30, quantity=500)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=6, action="sell",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 15
        assert quote["total"] == 90

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=6),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["net_earnings"] == quote["total"]


@pytest.mark.asyncio
class TestQuoteIsReadOnly:
    """The FakeSession here registers ONLY Station + MarketPrice -- a Player
    or Ship query (which would imply a lock/re-fetch) raises AssertionError,
    structurally proving /quote never touches either row. Combined with the
    value assertions below (nothing mutated, nothing committed, nothing
    added), this is the WO's READ-ONLY acceptance criterion."""

    async def test_quote_mutates_nothing_and_commits_nothing(self):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _quote_session(station, mp)

        await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=10, action="buy",
            ),
            db=db, current_user=None, current_player=player,
        )

        assert player.credits == 10_000
        assert mp.buy_price == 20
        assert mp.sell_price == 30
        assert mp.quantity == 500
        assert db.commit_calls == 0
        assert db.added == []

    async def test_quote_rejects_invalid_action(self):
        from fastapi import HTTPException
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)

        with pytest.raises(HTTPException) as exc_info:
            await get_trade_quote(
                quote_request=TradeQuoteRequest(
                    station_id=str(station.id), resource_type="ore", quantity=10, action="trade",
                ),
                db=_quote_session(station, mp), current_user=None, current_player=player,
            )
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
class TestQuoteStockCheckMirrorsBuy:
    """mack LOW: /quote must reject an over-stock BUY quote the same way
    /buy would (station_quantity < requested quantity) -- otherwise a quote
    can price a trade the real commit path would 400 on. sell_resource has
    NO analogous stock cap (a station never runs out of room to buy FROM a
    player), so /quote must NOT gate sell on market_price.quantity either --
    both sides are pinned here."""

    async def test_buy_quote_rejects_quantity_exceeding_station_stock(self):
        from fastapi import HTTPException
        player = _neutral_player(credits=1_000_000)
        station = _neutral_station()
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=5)

        with pytest.raises(HTTPException) as exc_info:
            await get_trade_quote(
                quote_request=TradeQuoteRequest(
                    station_id=str(station.id), resource_type="ore", quantity=6, action="buy",
                ),
                db=_quote_session(station, mp), current_user=None, current_player=player,
            )
        assert exc_info.value.status_code == 400
        assert "5 units available" in exc_info.value.detail

    async def test_buy_quote_and_buy_route_reject_the_same_over_stock_quantity(self):
        """Parity proof: the SAME (station, quantity) that /buy 400s on must
        also 400 from /quote -- not just independently plausible numbers."""
        from fastapi import HTTPException
        player = _neutral_player(credits=1_000_000)
        station = _neutral_station()
        ship = _ship(capacity=1000)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=5)

        with pytest.raises(HTTPException) as quote_exc:
            await get_trade_quote(
                quote_request=TradeQuoteRequest(
                    station_id=str(station.id), resource_type="ore", quantity=6, action="buy",
                ),
                db=_quote_session(station, mp), current_user=None, current_player=player,
            )
        with pytest.raises(HTTPException) as buy_exc:
            await buy_resource(
                trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=6),
                db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
            )
        assert quote_exc.value.status_code == buy_exc.value.status_code == 400

    async def test_sell_quote_is_not_gated_by_station_stock(self):
        """sell_resource itself has no stock cap -- selling 6 units of "ore"
        to a station whose MarketPrice.quantity is only 5 succeeds on the
        real route, so /quote must price it too, not 400."""
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=6, contents={"ore": 6})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=5)

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=6, action="sell",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        assert quote["unit_price"] == 20
        assert quote["total"] == 120

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=6),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["net_earnings"] == quote["total"]


@pytest.mark.asyncio
class TestQuotePeeksHaggleWithoutConsuming:
    """An ACCEPTED-but-not-yet-consumed haggle price must be reflected in
    the quote (so the preview matches what buy_resource is about to charge)
    WITHOUT being consumed by the quote call itself -- consuming it here
    would make the immediately-following real trade fall back to the
    posted price instead, breaking quote == charge for exactly the
    scenario this feature exists to protect."""

    async def test_accepted_haggle_price_previewed_then_still_consumable_by_the_real_buy(self):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        haggle_key = f"{station.id}:ore:buy"
        player.settings = {
            "haggle": {
                "sessions": {haggle_key: {"status": "accepted", "round": 2, "agreed_price": 22.4}},
                "locks": {}, "cooldowns": {},
            }
        }

        quote = await get_trade_quote(
            quote_request=TradeQuoteRequest(
                station_id=str(station.id), resource_type="ore", quantity=5, action="buy",
            ),
            db=_quote_session(station, mp), current_user=None, current_player=player,
        )
        # int(round(22.4)) = 22, inside [15, 45] -- no band interference.
        assert quote["unit_price"] == 22
        assert quote["total"] == 110

        # Peek must NOT have consumed the session -- still "accepted".
        assert player.settings["haggle"]["sessions"][haggle_key]["status"] == "accepted"
        assert player.settings["haggle"]["sessions"][haggle_key]["agreed_price"] == 22.4

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=5),
            db=_trade_session(player, station, ship, mp), current_user=None, current_player=player,
        )
        assert result["transaction"]["unit_price"] == quote["unit_price"]
        assert result["transaction"]["total_with_tax"] == quote["total"]
        # The REAL trade's own consume_agreed_price call burns the session.
        assert player.settings["haggle"]["sessions"][haggle_key]["status"] == "consumed"
