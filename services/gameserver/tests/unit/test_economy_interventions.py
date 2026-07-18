"""Unit coverage for WO-ADM-ECON-TRUTH: the admin economy /intervention
endpoint's inject_liquidity and freeze_trading actions, plus the
INTERVENTION audit-write conditioning that wraps every intervention type.

Three lies fixed here (2H's sibling file, test_economy_analytics_vocab.py,
covers the fourth — _reset_market_prices — and is untouched):
  1. _inject_liquidity previously queried a Station and returned a dict with
     NO write at all — now persists real stock into station.commodities
     (the JSONB TradingService reprices from) AND the mirrored MarketPrice
     row (what GET /admin/economy/market-data and the buy/sell stock-gate
     both read), then re-syncs pricing off the new stock.
  2. _freeze_trading previously returned a canned success dict for an
     off-canon, zero-consumer capability — now raises InterventionError(501).
  3. perform_market_intervention wrote an unconditional INTERVENTION audit
     row on EVERY outcome, including failures — now a rejected/failed call
     writes no audit row at all (fixture-scoped delta assertions below,
     never a bare literal, per the project's audit-count convention).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from src.models.audit_log import AuditLog
from src.models.market_transaction import MarketPrice
from src.models.station import Station, StationClass, StationType
from src.services.audit_service import AuditAction
from src.services.economy_analytics_service import EconomyAnalyticsService, InterventionError


@pytest.fixture
def station(db: Session) -> Station:
    # commodities is left at the model default (models/station.py) — "ore"
    # ships at quantity=1000/capacity=5000, "fuel" at quantity=1500/capacity=4000.
    s = Station(
        id=uuid4(),
        name="Injection Test Station",
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


def _intervention_audit_count(db: Session) -> int:
    return db.query(AuditLog).filter(AuditLog.action == AuditAction.INTERVENTION.value).count()


@pytest.mark.unit
class TestInjectLiquidityPersists:
    def test_injected_stock_is_readable_by_the_market_query(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        """Confirm BOTH storages the trade paths read moved: the
        commodities JSONB (what TradingService reprices from) and the
        mirrored MarketPrice row (what GET /admin/economy/market-data and
        the buy/sell stock-gate — trading.py:393 — both read)."""
        result = service._inject_liquidity({
            "station_id": station.id,
            "resources": {"ore": 200},
        })

        assert result["resources_injected"] == {"ore": 200}

        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 1200

        market_price = db.query(MarketPrice).filter(
            MarketPrice.station_id == station.id,
            MarketPrice.commodity == "ore",
        ).first()
        assert market_price is not None
        assert market_price.quantity == 1200

    def test_injection_clamps_to_capacity(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        """ore ships at capacity=5000 (model default); injecting far past
        it must clamp — the same physical bound stock-regen advances
        toward (trading_service.py: quantity = min(capacity, quantity +
        production_rate))."""
        result = service._inject_liquidity({
            "station_id": station.id,
            "resources": {"ore": 999_999},
        })

        assert result["resources_injected"]["ore"] == 5000 - 1000  # capped at capacity
        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 5000

    def test_alias_resource_type_resolves_to_canonical_ore(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        """"fuel_ore" (citadel/planet domain) resolves to canonical "ore"
        (COMMODITY_ALIASES) — the same alias 2H's
        test_fuel_ore_alias_resolves_to_canonical_ore_rows exercises for
        _reset_market_prices."""
        result = service._inject_liquidity({
            "station_id": station.id,
            "resources": {"fuel_ore": 100},
        })

        assert result["resources_injected"] == {"ore": 100}
        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 1100

    def test_unknown_resource_type_is_skipped_not_written(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        result = service._inject_liquidity({
            "station_id": station.id,
            "resources": {"ore": 100, "plasma": 500},
        })

        assert result["resources_injected"] == {"ore": 100}
        assert "plasma" in result["skipped"]
        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 1100
        assert "plasma" not in station.commodities

    def test_all_unknown_resources_rejected_no_write(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        with pytest.raises(ValueError):
            service._inject_liquidity({
                "station_id": station.id,
                "resources": {"plasma": 500},
            })
        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 1000  # untouched

    def test_negative_quantity_rejected(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        with pytest.raises(ValueError):
            service._inject_liquidity({
                "station_id": station.id,
                "resources": {"ore": -50},
            })
        db.refresh(station)
        assert station.commodities["ore"]["quantity"] == 1000

    def test_station_not_found_rejected(self, service: EconomyAnalyticsService):
        with pytest.raises(ValueError):
            service._inject_liquidity({
                "station_id": uuid4(),
                "resources": {"ore": 100},
            })


@pytest.mark.unit
class TestFreezeTradingIsHonest501:
    def test_freeze_trading_raises_501(self, service: EconomyAnalyticsService):
        with pytest.raises(InterventionError) as exc_info:
            service._freeze_trading({"duration_minutes": 60})
        assert exc_info.value.status_code == 501

    def test_freeze_trading_via_dispatcher_writes_no_audit_row(
        self, db: Session, service: EconomyAnalyticsService
    ):
        pre_count = _intervention_audit_count(db)

        with pytest.raises(InterventionError):
            service.perform_market_intervention(
                "freeze_trading", {"admin_id": uuid4(), "duration_minutes": 60}
            )

        assert _intervention_audit_count(db) == pre_count


@pytest.mark.unit
class TestAuditWriteConditionedOnCommittedMutation:
    def test_unknown_intervention_type_writes_no_audit_row(
        self, db: Session, service: EconomyAnalyticsService
    ):
        pre_count = _intervention_audit_count(db)

        with pytest.raises(ValueError):
            service.perform_market_intervention("not_a_real_type", {"admin_id": uuid4()})

        assert _intervention_audit_count(db) == pre_count

    def test_failed_inject_liquidity_writes_no_audit_row(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        pre_count = _intervention_audit_count(db)

        with pytest.raises(ValueError):
            service.perform_market_intervention(
                "inject_liquidity",
                {"admin_id": uuid4(), "station_id": station.id, "resources": {"plasma": 500}},
            )

        assert _intervention_audit_count(db) == pre_count

    def test_successful_inject_liquidity_writes_exactly_one_audit_row(
        self, db: Session, station: Station, service: EconomyAnalyticsService
    ):
        pre_count = _intervention_audit_count(db)

        result = service.perform_market_intervention(
            "inject_liquidity",
            {"admin_id": uuid4(), "station_id": station.id, "resources": {"ore": 100}},
        )

        assert result["status"] == "success"
        assert _intervention_audit_count(db) == pre_count + 1
