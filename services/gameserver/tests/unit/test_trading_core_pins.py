"""DB-free regression pins for the trading core loop (WO-QTI-CORELOOP-PINS
Lane 1): buy/sell execution price math, the commodity-economy spread/premium
stack, and cargo/credit/turns accounting across a buy->sell round trip.

Dedupe: no existing suite pins TradingService.calculate_dynamic_price's
SELL_SPREAD/BUY_SPREAD/class-premium composition, the buy/sell route's
cargo+credit mutation, or the "docked trade costs 0 turns" invariant --
these are all new ground. (test_price_history_sweep.py pins the
PriceHistory sweep, a disjoint concern; test_docking_turns.py pins dock/
undock turn cost, not trade execution.)

Two test tiers:

  TestDynamicPriceSpread / TestCommodityBandClamp / TestSellNeverBelowBuy
    -- pure TradingService(db=None) / clamp_to_commodity_band calls. Fully
    DB-free by construction: calculate_dynamic_price never queries the DB
    for its core math (the one DB read, get_active_event_modifiers, is
    wrapped in its own try/except and degrades to a 0.0 neutral delta on
    any db failure -- confirmed by passing db=None here).

  TestBuySellExecution -- calls the REAL buy_resource/sell_resource route
    coroutines directly (mirrors tests/unit/test_docking_turns.py's
    direct-route-call convention) against a hand-built fake Session
    (mirrors test_warp_gate_toll.py's _FakeQuery/_FakeSession pattern,
    generalized to key by an InstrumentedAttribute's owning class so
    db.query(Model) and db.query(Model.column) share one spec). The
    station/player/ship are chosen so every optional modifier (faction
    rep, personal rep, region tariff, station lever, rank bonus, medals)
    resolves to its own defensively-defended NEUTRAL value with zero
    additional queries -- this isolates the pins to the core trade math
    (spread x quantity, cargo delta, 0 turns) without needing to fake out
    reputation/medal/ranking subsystems this lane doesn't own.

    The player-reputation "peaceful trade" nudge (PersonalReputationService.
    adjust_reputation, always attempted, wrapped in its own try/except) is
    deliberately made a no-op by scripting its Player re-query to miss (the
    FakeQuery `seq` mechanism, same trick as test_warp_gate_toll.py's
    "owner vanishes mid-transaction" test) -- this keeps the round-trip
    price math independent of an unrelated subsystem's tier-boundary
    rounding, which is out of this lane's scope to pin.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.trading import TradeRequest, buy_resource, sell_resource
from src.models.market_transaction import MarketPrice
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType
from src.services.trading_service import (
    BUY_SPREAD,
    CLASS_8_BUY_PREMIUM,
    CLASS_9_SELL_PREMIUM,
    SELL_SPREAD,
    COMMODITY_PRICE_RANGES,
    TradingService,
    clamp_to_commodity_band,
)
from src.api.routes.trading import _reprice_after_trade


# ---------------------------------------------------------------------------
# Tier 1: pure TradingService(db=None) price math
# ---------------------------------------------------------------------------


def _station(*, station_class=StationClass.CLASS_1, commodities=None) -> Station:
    return Station(
        id=uuid.uuid4(),
        name="Test Station",
        sector_id=1,
        station_class=station_class,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities=commodities or {},
    )


def _commodity(*, quantity, capacity, base_price, buys=True, sells=True):
    return {
        "quantity": quantity,
        "capacity": capacity,
        "base_price": base_price,
        "buys": buys,
        "sells": sells,
    }


class TestDynamicPriceSpread:
    """SELL_SPREAD=1.15 / BUY_SPREAD=0.85 (trading_service.py) applied to the
    supply/demand midpoint. base_price=100, quantity==capacity/2 puts
    supply_ratio exactly at 0.5 -> midpoint == base_price (1.5-0.5=1.0), so
    the raw pre-clamp price is exactly base_price * spread -- no rounding
    ambiguity."""

    def test_sell_price_applies_1_15_spread_at_midpoint_supply(self):
        assert SELL_SPREAD == pytest.approx(1.15)
        station = _station(commodities={
            "equipment": _commodity(quantity=50, capacity=100, base_price=100),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "sell")
        # 100 * (1.5 - 0.5) * 1.15 = 115, inside equipment's [50, 120] band.
        assert price == 115

    def test_buy_price_applies_0_85_spread_at_midpoint_supply(self):
        assert BUY_SPREAD == pytest.approx(0.85)
        station = _station(commodities={
            "equipment": _commodity(quantity=50, capacity=100, base_price=100),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "buy")
        # 100 * 1.0 * 0.85 = 85, inside [50, 120].
        assert price == 85

    def test_supply_ratio_zero_yields_max_scarcity_premium_before_spread(self):
        # supply_ratio=0 -> midpoint = base*(1.5-0) = base*1.5; sell = *1.15.
        station = _station(commodities={
            "equipment": _commodity(quantity=0, capacity=100, base_price=40),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "sell")
        # 40 * 1.5 * 1.15 = 69
        assert price == 69

    def test_supply_ratio_one_yields_max_surplus_discount_before_spread(self):
        # supply_ratio=1 -> midpoint = base*(1.5-1) = base*0.5; buy = *0.85.
        # base_price=120 keeps the pre-clamp result (51) inside equipment's
        # [50, 120] band -- a lower base would be swallowed by the min clamp,
        # which is TestCommodityBandClamp's concern, not this one.
        station = _station(commodities={
            "equipment": _commodity(quantity=100, capacity=100, base_price=120),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "buy")
        # 120 * 0.5 * 0.85 = 51
        assert price == 51

    def test_class_8_buy_premium_1_2x_applies_when_buys_only(self):
        assert CLASS_8_BUY_PREMIUM == pytest.approx(1.2)
        station = _station(station_class=StationClass.CLASS_8, commodities={
            "equipment": _commodity(quantity=50, capacity=100, base_price=100, buys=True, sells=False),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "buy")
        # 100 * 1.0 * 0.85 * 1.2 = 102
        assert price == 102

    def test_class_9_sell_premium_1_25x_applies_when_sells_only(self):
        assert CLASS_9_SELL_PREMIUM == pytest.approx(1.25)
        station = _station(station_class=StationClass.CLASS_9, commodities={
            "equipment": _commodity(quantity=50, capacity=100, base_price=100, buys=False, sells=True),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "sell")
        # 100 * 1.0 * 1.15 * 1.25 = 143.75 -> clamped to equipment max 120
        assert price == 120

    def test_class_8_premium_skipped_when_commodity_carries_both_flags(self):
        """Exclusivity gate regression: the model's default dict ships some
        commodities with BOTH buys and sells True -- the Class-8 premium
        must NOT fire for those (it's a one-directional canon pattern)."""
        station = _station(station_class=StationClass.CLASS_8, commodities={
            "equipment": _commodity(quantity=50, capacity=100, base_price=100, buys=True, sells=True),
        })
        price = TradingService(None).calculate_dynamic_price(station, "equipment", "buy")
        # No premium: 100 * 1.0 * 0.85 = 85 (NOT 102).
        assert price == 85


class TestCommodityBandClamp:
    def test_clamp_floors_at_commodity_min(self):
        assert COMMODITY_PRICE_RANGES["ore"] == {"min": 15, "max": 45}
        assert clamp_to_commodity_band("ore", 1) == 15

    def test_clamp_ceilings_at_commodity_max(self):
        assert clamp_to_commodity_band("ore", 999) == 45

    def test_clamp_unknown_commodity_only_floors_at_1(self):
        assert clamp_to_commodity_band("not_a_real_commodity", 500) == 500
        assert clamp_to_commodity_band("not_a_real_commodity", -5) == 1


class TestSellNeverBelowBuy:
    """_reprice_after_trade's post-trade guard (routes/trading.py): if a
    degenerate repricing would leave buy_price >= sell_price, buy is forced
    to sell_price - 1 (floored at 1) so the station never loses money on the
    spread. TradingService(None) is safe here -- calculate_dynamic_price
    never needs a real db for this station (empty commodities -> both sides
    resolve to the 0-price "not found" branch)."""

    def test_guard_forces_buy_below_sell_on_degenerate_reprice(self):
        station = _station(commodities={})  # commodity absent -> both calc to 0
        market_price = MarketPrice(
            station_id=uuid.uuid4(), commodity="ore",
            buy_price=20, sell_price=30, quantity=10,
        )
        fired = _reprice_after_trade(None, station, market_price, "ore")
        assert fired is False  # no PriceAlert row possible without a real db
        # calculate_dynamic_price("ore") -> commodity missing -> 0 for both
        # sides; guard: new_buy(0) >= new_sell(0) -> new_buy = max(1, -1) = 1.
        assert market_price.sell_price == 0
        assert market_price.buy_price == 1


# ---------------------------------------------------------------------------
# Tier 2: full buy_resource / sell_resource route execution
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Stands in for a SQLAlchemy Query bound to one model. filter() /
    populate_existing() / with_for_update() are no-ops returning self --
    mirrors test_warp_gate_toll.py's _FakeQuery. `seq`, when given,
    supports a query shape hit MORE THAN ONCE with DIFFERENT results,
    consumed in call order; once exhausted every further call returns
    None (used to make the personal-reputation re-query miss)."""

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
    """Maps a model class to its fake query. Keys by the owning class for
    BOTH `db.query(Model)` and `db.query(Model.column)` (InstrumentedAttribute
    exposes `.class_`). A query for an unspecified model raises AssertionError
    -- deliberate: every trade-adjacent side effect this lane doesn't own
    (medals, ranking, faction/emergent rep, price alerts, event modifiers) is
    individually wrapped in its own try/except in the route/service code, so
    an AssertionError here proves those defensive wrappers are still doing
    their job, rather than silently mocking them away."""

    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.added = []
        self.commit_calls = 0
        self.flush_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_calls += 1

    def flush(self) -> None:
        self.flush_calls += 1

    def rollback(self) -> None:
        pass


def _neutral_player(*, credits: int, turns: int = 50) -> Player:
    """A Player with every optional trade modifier at its NEUTRAL value:
    Recruit rank (0% trading bonus/discount), Neutral reputation tier
    (1.0x), no first-login trade bonus, no team."""
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credits=credits,
        turns=turns,
        current_sector_id=1,
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
    """faction_affiliation/region_id/owner_id all None -> every price
    modifier (faction rep, region tariff, station lever) and the trade tax
    resolve to neutral/zero with ZERO extra db queries. commodities={} so
    _ensure_market_prices short-circuits (`if not station.commodities:
    return`) before ever touching MarketPrice -- the lazy stock-regen tick
    is out of this lane's scope (owned by the price-spread tests above)."""
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


def _session_for(player: Player, station: Station, ship: Ship, market_price: MarketPrice,
                  *, player_seq_len: int) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(seq=[player, None] * player_seq_len),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
    })


@pytest.fixture(autouse=True)
def _quiet_websocket_pushes():
    """The route's post-commit real-time pushes (_publish_trade_tick /
    _emit_transaction_completed) are already individually try/except-guarded
    in production code, but patch them out here anyway so this lane's pins
    assert on trade math, not on realtime-bus plumbing this lane doesn't own."""
    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.asyncio
class TestBuySellExecution:
    """ore is priced inside its canon [15, 45] band (COMMODITY_PRICE_RANGES)
    so a neutral-modifier trade charges/pays EXACTLY the posted MarketPrice
    with no clamp interference: buy charges market_price.sell_price, sell
    pays market_price.buy_price (routes/trading.py's documented BUY-charges-
    sell_price / SELL-pays-buy_price convention)."""

    async def test_buy_charges_exact_posted_price_and_fills_cargo(self):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert result["transaction"]["unit_price"] == 30
        assert result["transaction"]["total_cost"] == 300
        assert player.credits == 10_000 - 300
        assert ship.cargo["used"] == 10
        assert ship.cargo["contents"]["ore"] == 10
        assert db.commit_calls == 1

    async def test_sell_pays_exact_posted_price_and_drains_cargo(self):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert result["transaction"]["unit_price"] == 20
        assert result["transaction"]["total_earnings"] == 200
        assert player.credits == 1_000 + 200
        assert ship.cargo["used"] == 0
        assert "ore" not in ship.cargo["contents"]

    async def test_buy_and_sell_cost_zero_turns(self):
        player = _neutral_player(credits=10_000, turns=42)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=5),
            db=db, current_user=None, current_player=player,
        )
        assert player.turns == 42  # unchanged

        ship.cargo["used"] = 5
        ship.cargo["contents"]["ore"] = 5
        db2 = _session_for(player, station, ship, mp, player_seq_len=1)
        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=5),
            db=db2, current_user=None, current_player=player,
        )
        assert player.turns == 42  # still unchanged

    async def test_buy_then_sell_round_trip_cargo_returns_to_zero(self):
        """Cargo zero-sum: buying N then selling the same N back leaves
        used==0 and the commodity key absent from contents -- no leakage
        either direction. Each leg gets its OWN MarketPrice instance (a
        fresh 20/30 quote) rather than sharing one: _reprice_after_trade
        (routes/trading.py) recomputes buy/sell in place after every trade,
        and with this station's commodities JSONB empty (out of scope here
        -- see TestDynamicPriceSpread) that recompute degenerates to a
        "commodity not found" 0/1 price. A shared MarketPrice object would
        let the buy leg's post-trade reprice corrupt the price the sell leg
        reads, which is a fake-session artifact, not a real-server one (a
        real station always carries the commodities JSONB the price was
        seeded from)."""
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)

        db_buy = _session_for(
            player, station, ship,
            _market_price(station.id, buy_price=20, sell_price=30, quantity=500),
            player_seq_len=1,
        )
        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db_buy, current_user=None, current_player=player,
        )
        assert ship.cargo["used"] == 10

        db_sell = _session_for(
            player, station, ship,
            _market_price(station.id, buy_price=20, sell_price=30, quantity=500),
            player_seq_len=1,
        )
        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db_sell, current_user=None, current_player=player,
        )
        assert ship.cargo["used"] == 0
        assert "ore" not in ship.cargo["contents"]

    async def test_buy_then_sell_round_trip_credit_delta_equals_spread_margin(self):
        """Credits are NOT zero-sum by design -- the buy/sell spread is the
        station's margin -- but the net delta must equal EXACTLY
        quantity * (sell_price - buy_price), with no rounding leakage,
        since both prices land cleanly inside the commodity band at these
        magnitudes (no clamp, no rank/rep drift: Recruit + Neutral tier
        throughout, confirmed by the reputation-nudge suppression above).
        Separate MarketPrice instances per leg -- see the cargo round-trip
        test's docstring for why."""
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        starting_credits = player.credits

        db_buy = _session_for(
            player, station, ship,
            _market_price(station.id, buy_price=20, sell_price=30, quantity=500),
            player_seq_len=1,
        )
        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db_buy, current_user=None, current_player=player,
        )
        db_sell = _session_for(
            player, station, ship,
            _market_price(station.id, buy_price=20, sell_price=30, quantity=500),
            player_seq_len=1,
        )
        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db_sell, current_user=None, current_player=player,
        )

        # 10 * (30 - 20) = 100 cr net cost -- the spread margin, exactly.
        assert starting_credits - player.credits == 100

    async def test_buy_rejects_insufficient_credits_with_zero_mutation(self):
        player = _neutral_player(credits=100)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await buy_resource(
                trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
                db=db, current_user=None, current_player=player,
            )
        assert exc_info.value.status_code == 400
        assert player.credits == 100  # untouched
        assert ship.cargo["used"] == 0  # untouched


def _bonus_player(*, credits: int, turns: int = 50) -> Player:
    """Same as `_neutral_player` but carrying the ADR-0026 FL1 lifetime
    first-login negotiation trade bonus (`Player.settings["trade_bonus"]
    = 0.1`, written by `first_login_service.complete_first_login` when
    `negotiation_skill` is STRONG and the awarded ship's `rarity_tier >= 3`
    -- see `tests/unit/test_first_login_persistence.py` for the write-path
    pin). Every other modifier stays neutral, isolating the price delta to
    this one term."""
    player = _neutral_player(credits=credits, turns=turns)
    player.settings = {"trade_bonus": 0.1}
    return player


@pytest.mark.asyncio
class TestFirstLoginTradeBonusPricing:
    """Pins the ADR-0026 FL1 term of `compute_player_price_multiplier`
    (trading_service.py) at the route level, through the single shared
    buy+sell multiplier site `compute_effective_unit_price` calls
    (trading.py:278).

    Two claims:

    1. A bonus player trades exactly 10% better than a neutral twin -- BUY
       charges 90% of the posted price; SELL pays 1 / 0.9 of it (the
       documented divide-on-sell inversion, symmetric with the rank/rep
       layers).
    2. `_neutral_player`'s `settings={}` (used throughout
       `TestBuySellExecution` above) already IS the no-bonus-set legacy
       shape -- `Player.settings` is `Column(JSONB, nullable=False,
       default={})` (models/player.py:153), so a never-migrated /
       never-earned-the-bonus player's settings can only ever be `{}`, not
       `None`. `test_buy_charges_exact_posted_price_and_fills_cargo` /
       `test_sell_pays_exact_posted_price_and_drains_cargo` charging exactly
       the posted `MarketPrice` (no discount) ARE the no-bonus regression
       pin for this feature -- this class doesn't duplicate them, it makes
       the cross-reference explicit so the "byte-identical for legacy
       players" claim is provable from the suite.
    """

    async def test_buy_charges_10pct_less_for_bonus_player_than_neutral_twin(self):
        player = _bonus_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        # Neutral twin charges exactly the posted sell_price (30/unit --
        # see test_buy_charges_exact_posted_price_and_fills_cargo). The
        # bonus player must charge exactly 10% less: int(30 * 0.9) == 27,
        # comfortably inside ore's [15, 45] canon band so the final clamp
        # never interferes.
        assert result["transaction"]["unit_price"] == 27
        assert result["transaction"]["total_cost"] == 270
        assert player.credits == 10_000 - 270

    async def test_sell_pays_10pct_more_for_bonus_player_than_neutral_twin(self):
        player = _bonus_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        # Neutral twin is paid exactly the posted buy_price (20/unit --
        # see test_sell_pays_exact_posted_price_and_drains_cargo). SELL
        # divides by the player-pays multiplier (a favoured player is paid
        # MORE): int(20 / 0.9) == 22.
        assert result["transaction"]["unit_price"] == 22
        assert result["transaction"]["total_earnings"] == 220
        assert player.credits == 1_000 + 220


class TestNeutralPlayerIsTheLegacyNoBonusShape:
    """Not asyncio -- kept as its own plain class (rather than a method on
    TestFirstLoginTradeBonusPricing) so it doesn't inherit that class's
    `@pytest.mark.asyncio` mark. Ties claim 2 above to a concrete assertion:
    `_neutral_player`'s settings really is the same `{}` shape a
    legacy/no-bonus player's row carries, and reading `trade_bonus` off it
    really is neutral (0.0) -- exactly what `compute_player_price_multiplier`
    reads."""

    def test_neutral_player_settings_is_empty_dict_not_none(self):
        player = _neutral_player(credits=0)
        assert player.settings == {}
        assert player.settings.get("trade_bonus", 0.0) == 0.0


class TestDockedTradeNeverSpendsTurns:
    """Structural pin (mirrors test_warp_gate_toll.py's inspect.getsource
    technique): buy_resource/sell_resource must never call spend_turns at
    all -- a much stronger, refactor-proof guarantee than "turns happened to
    be unchanged in one scenario" alone."""

    def test_buy_resource_source_never_calls_spend_turns(self):
        import inspect
        from src.api.routes import trading as trading_routes
        source = inspect.getsource(trading_routes.buy_resource)
        assert "spend_turns" not in source

    def test_sell_resource_source_never_calls_spend_turns(self):
        import inspect
        from src.api.routes import trading as trading_routes
        source = inspect.getsource(trading_routes.sell_resource)
        assert "spend_turns" not in source
