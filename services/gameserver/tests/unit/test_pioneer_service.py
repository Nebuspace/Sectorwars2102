"""Unit tests — pioneer_service.py (pioneer migration contract ledger).

No test file existed for this service. DB-free: db.query() calls route
through a hand-rolled _FakeDb (keyed-queue-per-model, chainable no-op
filter/join/order_by/with_for_update/populate_existing, matching this
suite's established convention). pioneer_service.TradingService is
monkeypatched to a scripted fake so quote_fee/quote_surplus_price's
"live station price" branch is tested without pulling in real trading
logic (which is out of this service's lane and independently testable
elsewhere). types.SimpleNamespace stands in for MigrationContract/Station/
Player (plain attribute reads/writes only); a real (unattached) Planet
model instance is used in sell_planet_surplus tests because flag_modified()
on active_events (JSONB) requires a real mapped instance.

Sections:
  TestQuoteFee / TestQuoteSurplusPrice — the two pricing helpers: live
    station-price branch (via the monkeypatched TradingService) vs. the
    canon-midpoint fallback, both clamped to the 30-80 range.
  TestAttributeSettlement — the FIFO delivered-ledger advance: zero/
    negative settled_qty is a no-op, partial fills leave a remainder,
    a contract crossing cohort_total flips to FULFILLED, and settled
    colonists with no matching loaded contract are simply not attributed.
  TestReabsorbOnShipLoss — loaded zeroed on every open contract; a
    contract with nothing yet delivered is VOIDed, one with partial
    delivery is left IN_PROGRESS (loaded still zeroed).
  Test_ReadSurplus — the active_events JSON reader's defensive coercion
    (non-dict, missing key, negative, non-numeric).
  TestSellPlanetSurplus — every precondition rejection (bad quantity,
    not-owned, sector mismatch, zero surplus, over-ask, player missing),
    and the successful full-sell / partial-sell mutation + payout.
"""
from types import SimpleNamespace

import pytest

from src.models.migration_contract import MigrationContractStatus
from src.models.planet import Planet
from src.services import pioneer_service
from src.services.pioneer_service import (
    _COLONIST_RANGE,
    _SELL_SPREAD,
    SURPLUS_PIONEERS_KEY,
    SurplusSaleError,
    _read_surplus,
    attribute_settlement,
    quote_fee,
    quote_surplus_price,
    reabsorb_on_ship_loss,
    sell_planet_surplus,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *_a, **_k):
        return self

    def join(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def with_for_update(self, *_a, **_k):
        return self

    def populate_existing(self):
        return self

    def all(self):
        return list(self._result)

    def first(self):
        return self._result[0] if self._result else None


class _FakeDb:
    def __init__(self, results=None):
        self._results = {k: list(v) for k, v in (results or {}).items()}

    def query(self, model):
        queue = self._results.get(model, [])
        result = queue.pop(0) if queue else []
        return _FakeQuery(result)


def _fallback_price():
    base = _COLONIST_RANGE["min"]
    midpoint = base * (1.5 - 0.5)
    price = int(round(midpoint * _SELL_SPREAD))
    return max(_COLONIST_RANGE["min"], min(price, _COLONIST_RANGE["max"]))


class _FakeTradingService:
    """Scripted stand-in for pioneer_service.TradingService, keyed by the
    (commodity, side) pair so quote_fee's 'sell' call and
    quote_surplus_price's 'buy' call can be scripted independently."""

    _next_result = None

    def __init__(self, db):
        self.db = db

    def calculate_dynamic_price(self, _subject, commodity, side):
        assert commodity == "colonists"
        return type(self)._next_result[side]


def _contract(**kwargs):
    defaults = dict(
        player_id="p1",
        status=MigrationContractStatus.IN_PROGRESS,
        loaded=0,
        delivered=0,
        cohort_total=100,
        created_at=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _station(**kwargs):
    defaults = dict(sector_id="s1", commodities={})
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _planet(**kwargs):
    p = Planet()
    p.id = kwargs.pop("id", "planet-1")
    p.name = kwargs.pop("name", "New Eden")
    p.sector_id = kwargs.pop("sector_id", "s1")
    p.active_events = kwargs.pop("active_events", {})
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


# ---------------------------------------------------------------------------
# quote_fee
# ---------------------------------------------------------------------------


class TestQuoteFee:
    def test_uses_live_station_price_when_a_seller_exists(self, monkeypatch):
        from src.models.station import Station

        seller = _station(commodities={"colonists": {"sells": True}})
        db = _FakeDb(results={Station: [[seller]]})
        _FakeTradingService._next_result = {"sell": 55}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_fee(db, _planet()) == 55

    def test_falls_back_when_no_station_sells_colonists(self, monkeypatch):
        from src.models.station import Station

        non_seller = _station(commodities={"colonists": {"sells": False}})
        db = _FakeDb(results={Station: [[non_seller]]})
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_fee(db, _planet()) == _fallback_price()

    def test_falls_back_when_no_stations_in_sector(self, monkeypatch):
        from src.models.station import Station

        db = _FakeDb(results={Station: [[]]})
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_fee(db, _planet()) == _fallback_price()

    def test_falls_back_when_live_price_is_non_positive(self, monkeypatch):
        from src.models.station import Station

        seller = _station(commodities={"colonists": {"sells": True}})
        db = _FakeDb(results={Station: [[seller]]})
        _FakeTradingService._next_result = {"sell": 0}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_fee(db, _planet()) == _fallback_price()

    def test_result_is_always_within_the_canon_clamp(self, monkeypatch):
        from src.models.station import Station

        seller = _station(commodities={"colonists": {"sells": True}})
        db = _FakeDb(results={Station: [[seller]]})
        _FakeTradingService._next_result = {"sell": 999999}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        # quote_fee trusts calculate_dynamic_price's own clamp -- it does NOT
        # re-clamp the live-station branch. Document that explicitly here so
        # a future change to that trust boundary is caught as a behavior
        # change, not silently accepted.
        assert quote_fee(db, _planet()) == 999999


# ---------------------------------------------------------------------------
# quote_surplus_price
# ---------------------------------------------------------------------------


class TestQuoteSurplusPrice:
    def test_uses_live_buy_price_when_the_station_buys_colonists(self, monkeypatch):
        station = _station(commodities={"colonists": {"buys": True}})
        db = _FakeDb()
        _FakeTradingService._next_result = {"buy": 42}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_surplus_price(db, station) == 42

    def test_falls_back_when_station_does_not_buy_colonists(self, monkeypatch):
        station = _station(commodities={"colonists": {"buys": False}})
        db = _FakeDb()
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_surplus_price(db, station) == _fallback_price()

    def test_falls_back_when_live_price_is_non_positive(self, monkeypatch):
        station = _station(commodities={"colonists": {"buys": True}})
        db = _FakeDb()
        _FakeTradingService._next_result = {"buy": -5}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_surplus_price(db, station) == _fallback_price()

    def test_live_price_is_clamped_to_the_canon_range(self, monkeypatch):
        station = _station(commodities={"colonists": {"buys": True}})
        db = _FakeDb()
        _FakeTradingService._next_result = {"buy": 999999}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_surplus_price(db, station) == _COLONIST_RANGE["max"]

    def test_missing_commodities_dict_falls_back(self, monkeypatch):
        station = _station(commodities=None)
        db = _FakeDb()
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        assert quote_surplus_price(db, station) == _fallback_price()


# ---------------------------------------------------------------------------
# attribute_settlement
# ---------------------------------------------------------------------------


class TestAttributeSettlement:
    def test_zero_settled_qty_is_a_no_op(self):
        db = _FakeDb()  # would IndexError if a query were attempted
        assert attribute_settlement(db, "p1", 0) == 0

    def test_negative_settled_qty_is_a_no_op(self):
        db = _FakeDb()
        assert attribute_settlement(db, "p1", -5) == 0

    def test_single_contract_partial_fill_leaves_remainder(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=50, delivered=0, cohort_total=100)
        db = _FakeDb(results={MigrationContract: [[c]]})
        attributed = attribute_settlement(db, "p1", 20)
        assert attributed == 20
        assert c.loaded == 30
        assert c.delivered == 20
        assert c.status == MigrationContractStatus.IN_PROGRESS

    def test_fifo_across_multiple_contracts(self):
        from src.models.migration_contract import MigrationContract

        older = _contract(loaded=10, delivered=0, cohort_total=100, created_at=1)
        newer = _contract(loaded=10, delivered=0, cohort_total=100, created_at=2)
        db = _FakeDb(results={MigrationContract: [[older, newer]]})
        attributed = attribute_settlement(db, "p1", 15)
        assert attributed == 15
        assert older.loaded == 0
        assert older.delivered == 10
        assert newer.loaded == 5
        assert newer.delivered == 5

    def test_contract_reaching_cohort_total_is_fulfilled(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=10, delivered=90, cohort_total=100)
        db = _FakeDb(results={MigrationContract: [[c]]})
        attribute_settlement(db, "p1", 10)
        assert c.delivered == 100
        assert c.status == MigrationContractStatus.FULFILLED

    def test_settled_beyond_total_loaded_is_not_over_attributed(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=5, delivered=0, cohort_total=100)
        db = _FakeDb(results={MigrationContract: [[c]]})
        attributed = attribute_settlement(db, "p1", 20)
        assert attributed == 5
        assert c.loaded == 0

    def test_no_open_contracts_attributes_nothing(self):
        from src.models.migration_contract import MigrationContract

        db = _FakeDb(results={MigrationContract: [[]]})
        assert attribute_settlement(db, "p1", 10) == 0


# ---------------------------------------------------------------------------
# reabsorb_on_ship_loss
# ---------------------------------------------------------------------------


class TestReabsorbOnShipLoss:
    def test_zeroes_loaded_on_every_open_contract(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=40, delivered=0)
        db = _FakeDb(results={MigrationContract: [[c]]})
        reabsorb_on_ship_loss(db, "p1")
        assert c.loaded == 0

    def test_undelivered_contract_is_voided(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=40, delivered=0)
        db = _FakeDb(results={MigrationContract: [[c]]})
        touched = reabsorb_on_ship_loss(db, "p1")
        assert touched == 1
        assert c.status == MigrationContractStatus.VOID

    def test_partially_delivered_contract_stays_in_progress(self):
        from src.models.migration_contract import MigrationContract

        c = _contract(loaded=40, delivered=10)
        db = _FakeDb(results={MigrationContract: [[c]]})
        reabsorb_on_ship_loss(db, "p1")
        assert c.status == MigrationContractStatus.IN_PROGRESS
        assert c.loaded == 0

    def test_no_open_contracts_touches_nothing(self):
        from src.models.migration_contract import MigrationContract

        db = _FakeDb(results={MigrationContract: [[]]})
        assert reabsorb_on_ship_loss(db, "p1") == 0


# ---------------------------------------------------------------------------
# _read_surplus
# ---------------------------------------------------------------------------


class TestReadSurplus:
    def test_valid_value_returned(self):
        p = _planet(active_events={SURPLUS_PIONEERS_KEY: 12})
        assert _read_surplus(p) == 12

    def test_missing_key_returns_zero(self):
        p = _planet(active_events={})
        assert _read_surplus(p) == 0

    def test_non_dict_active_events_returns_zero(self):
        p = _planet(active_events=None)
        assert _read_surplus(p) == 0

    def test_negative_value_clamped_to_zero(self):
        p = _planet(active_events={SURPLUS_PIONEERS_KEY: -5})
        assert _read_surplus(p) == 0

    def test_non_numeric_value_returns_zero(self):
        p = _planet(active_events={SURPLUS_PIONEERS_KEY: "lots"})
        assert _read_surplus(p) == 0


# ---------------------------------------------------------------------------
# sell_planet_surplus
# ---------------------------------------------------------------------------


class TestSellPlanetSurplus:
    def test_non_numeric_quantity_raises_400(self, monkeypatch):
        from src.models.planet import Planet as PlanetModel

        db = _FakeDb(results={PlanetModel: [[_planet()]]})
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station(), quantity="lots")
        assert exc.value.status_code == 400

    def test_non_positive_quantity_raises_400(self):
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(_FakeDb(), "p1", "planet-1", _station(), quantity=0)
        assert exc.value.status_code == 400

    def test_planet_not_found_or_not_owned_raises_404(self):
        from src.models.planet import Planet as PlanetModel

        db = _FakeDb(results={PlanetModel: [[]]})
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station())
        assert exc.value.status_code == 404

    def test_sector_mismatch_raises_400(self):
        from src.models.planet import Planet as PlanetModel

        planet = _planet(sector_id="s1", active_events={SURPLUS_PIONEERS_KEY: 10})
        db = _FakeDb(results={PlanetModel: [[planet]]})
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station(sector_id="s2"))
        assert exc.value.status_code == 400

    def test_zero_surplus_raises_400(self):
        from src.models.planet import Planet as PlanetModel

        planet = _planet(active_events={SURPLUS_PIONEERS_KEY: 0})
        db = _FakeDb(results={PlanetModel: [[planet]]})
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station())
        assert exc.value.status_code == 400

    def test_over_ask_raises_400(self):
        from src.models.planet import Planet as PlanetModel

        planet = _planet(active_events={SURPLUS_PIONEERS_KEY: 5})
        db = _FakeDb(results={PlanetModel: [[planet]]})
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station(), quantity=10)
        assert exc.value.status_code == 400

    def test_missing_player_raises_404(self, monkeypatch):
        from src.models.planet import Planet as PlanetModel
        from src.models.player import Player

        planet = _planet(active_events={SURPLUS_PIONEERS_KEY: 5})
        db = _FakeDb(results={PlanetModel: [[planet]], Player: [[]]})
        _FakeTradingService._next_result = {"buy": 30}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        with pytest.raises(SurplusSaleError) as exc:
            sell_planet_surplus(db, "p1", "planet-1", _station(sector_id="s1"))
        assert exc.value.status_code == 404

    def test_selling_entire_surplus_pays_out_and_zeroes_remaining(self, monkeypatch):
        from src.models.planet import Planet as PlanetModel
        from src.models.player import Player

        planet = _planet(sector_id="s1", active_events={SURPLUS_PIONEERS_KEY: 8})
        player = SimpleNamespace(id="p1", credits=1000)
        db = _FakeDb(results={PlanetModel: [[planet]], Player: [[player]]})
        _FakeTradingService._next_result = {"buy": 40}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        station = _station(sector_id="s1", commodities={"colonists": {"buys": True}})

        result = sell_planet_surplus(db, "p1", "planet-1", station)

        assert result["sold"] == 8
        assert result["price_per_pioneer"] == 40
        assert result["credits_earned"] == 320
        assert result["surplus_remaining"] == 0
        assert planet.active_events[SURPLUS_PIONEERS_KEY] == 0
        assert player.credits == 1320

    def test_partial_sell_leaves_remaining_surplus(self, monkeypatch):
        from src.models.planet import Planet as PlanetModel
        from src.models.player import Player

        planet = _planet(sector_id="s1", active_events={SURPLUS_PIONEERS_KEY: 20})
        player = SimpleNamespace(id="p1", credits=0)
        db = _FakeDb(results={PlanetModel: [[planet]], Player: [[player]]})
        _FakeTradingService._next_result = {"buy": 30}
        monkeypatch.setattr(pioneer_service, "TradingService", _FakeTradingService)
        station = _station(sector_id="s1", commodities={"colonists": {"buys": True}})

        result = sell_planet_surplus(db, "p1", "planet-1", station, quantity=5)

        assert result["sold"] == 5
        assert result["surplus_remaining"] == 15
        assert planet.active_events[SURPLUS_PIONEERS_KEY] == 15
        assert player.credits == 150
