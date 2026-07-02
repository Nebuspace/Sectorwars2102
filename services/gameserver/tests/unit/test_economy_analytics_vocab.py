"""Unit coverage for EconomyAnalyticsService's canon-vocabulary fixes
(WO-ARCH-RES-2H-RUNTIME-VOCAB).

Two bugs fixed here, both in this one file:
  1. _reset_market_prices wrote a hand-maintained, non-canon baseline table
     (fuel 100 vs canon base 12, six ghost slugs no writer ever used, a
     silent 100-credit default for anything unknown) — now derives from
     commodity_economy.base_price() and rejects unknown commodities outright
     (DATA bugfix only; admin route auth is untouched).
  2. Four ResourceType-iteration loops filtered on the UPPER_CASE enum
     .value ('ORE') against MarketPrice/PriceHistory.commodity rows that are
     always written lowercase ('ore') — every aggregate silently matched
     zero rows. Rewired to iterate COMMODITY_BASE_PRICES' lowercase slugs.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.models.market_transaction import MarketPrice, PriceHistory
from src.models.station import Station, StationClass, StationType
from src.services.economy_analytics_service import EconomyAnalyticsService


@pytest.fixture
def station(db: Session) -> Station:
    s = Station(
        id=uuid4(),
        name="Vocab Test Station",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
    )
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def service(db: Session) -> EconomyAnalyticsService:
    return EconomyAnalyticsService(db)


@pytest.mark.unit
class TestNoResourceTypeDependency:
    def test_resource_type_import_is_gone(self):
        import src.services.economy_analytics_service as module
        source = Path(inspect.getfile(module)).read_text()
        assert "ResourceType" not in source

    def test_all_four_loops_iterate_commodity_base_prices_keys(self):
        source = inspect.getsource(EconomyAnalyticsService)
        assert source.count("for resource_name in COMMODITY_BASE_PRICES") == 4


@pytest.mark.unit
class TestResetMarketPrices:
    def test_ore_resets_to_canonical_base(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        row = MarketPrice(station_id=station.id, commodity="ore", buy_price=999, sell_price=999, quantity=100)
        db.add(row)
        db.commit()

        result = service._reset_market_prices({"resource_type": "ore"})

        assert result["baseline_price"] == COMMODITY_BASE_PRICES["ore"]["base"] == 15
        assert result["affected_ports"] == 1
        db.refresh(row)
        assert row.buy_price == int(15 * 0.9)
        assert row.sell_price == int(15 * 1.1)

    def test_fuel_ore_alias_resolves_to_canonical_ore_rows(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        """The citadel/planet domain spells this commodity "fuel_ore"; every
        MarketPrice writer still stores rows under "ore" — the reset must
        filter on the canonical slug, not the alias, to find them."""
        row = MarketPrice(station_id=station.id, commodity="ore", buy_price=1, sell_price=1, quantity=1)
        db.add(row)
        db.commit()

        result = service._reset_market_prices({"resource_type": "fuel_ore"})

        assert result["baseline_price"] == 15
        assert result["affected_ports"] == 1

    def test_unknown_commodity_is_rejected_zero_rows_written(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        row = MarketPrice(station_id=station.id, commodity="ore", buy_price=42, sell_price=50, quantity=1)
        db.add(row)
        db.commit()

        result = service._reset_market_prices({"resource_type": "plasma"})

        assert "error" in result
        assert result["affected_ports"] == 0
        db.refresh(row)
        assert row.buy_price == 42  # untouched
        assert row.sell_price == 50

    def test_ghost_baseline_values_are_gone(self):
        source = inspect.getsource(EconomyAnalyticsService._reset_market_prices)
        for ghost in ("technology", "luxury_items", "raw_materials", "dark_matter", "bio_samples"):
            assert ghost not in source


@pytest.mark.unit
class TestFourAggregatesUseLowercaseCanonSlugs:
    """One seeded 'ore' row must surface under the lowercase 'ore' key in
    every aggregate — today it surfaces nowhere, because each loop filters
    on the UPPER_CASE ResourceType.value ('ORE') against a lowercase column."""

    def test_inflation_rates_key_on_lowercase_ore(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        now = datetime.utcnow()
        db.add(MarketPrice(station_id=station.id, commodity="ore", buy_price=30, sell_price=40, quantity=10))
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=15, sell_price=20, quantity=10,
            demand_level=1.0, supply_level=1.0, snapshot_date=now - timedelta(days=1),
        ))
        db.commit()

        inflation = service._calculate_inflation_rates()

        assert "ore" in inflation
        assert "ORE" not in inflation
        assert inflation["ore"] == 100.0  # (30 - 15) / 15 * 100

    def test_liquidity_spreads_present_for_lowercase_ore(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        db.add(MarketPrice(station_id=station.id, commodity="ore", buy_price=10, sell_price=20, quantity=10))
        db.commit()

        liquidity = service._calculate_market_liquidity()

        assert "ore" in liquidity["average_spreads"]
        assert "ORE" not in liquidity["average_spreads"]
        assert liquidity["average_spreads"]["ore"] == 50.0  # (20-10)/20*100

    def test_average_prices_present_and_nonzero_for_lowercase_ore(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        db.add(MarketPrice(station_id=station.id, commodity="ore", buy_price=20, sell_price=25, quantity=10))
        db.commit()

        prices = service._get_average_prices()

        assert prices.get("ore") == 20.0
        assert "ORE" not in prices

    def test_volatility_present_and_nonzero_for_lowercase_ore(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        now = datetime.utcnow()
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=10, sell_price=12, quantity=10,
            demand_level=1.0, supply_level=1.0, snapshot_date=now - timedelta(hours=1),
        ))
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=30, sell_price=32, quantity=10,
            demand_level=1.0, supply_level=1.0, snapshot_date=now - timedelta(hours=2),
        ))
        db.commit()

        volatility = service._calculate_price_volatility()

        assert volatility.get("ore", 0) > 0
        assert "ORE" not in volatility
