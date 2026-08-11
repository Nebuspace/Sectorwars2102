"""TradeDock premium pricing (WO-BUILD-TRADEDOCK-PREMIUM-PRICING-WIRE)."""
from __future__ import annotations

import uuid

import pytest

from src.models.station import Station, StationClass, StationStatus, StationType
from src.services.trading_service import (
    BUY_SPREAD,
    SELL_SPREAD,
    TRADEDOCK_BUY_SPREAD,
    TRADEDOCK_SELL_SPREAD,
    TradingService,
    inventory_capacity_for,
    is_tradedock,
    spreads_for,
    tradedock_bulk_discount_fraction,
    transaction_fee_rate,
)
from src.api.routes.trading import compute_buy_totals


def _station(*, tradedock_tier=None, commodities=None) -> Station:
    return Station(
        id=uuid.uuid4(),
        name="Dock",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        tradedock_tier=tradedock_tier,
        commodities=commodities or {},
    )


def test_is_tradedock_and_spreads():
    port = _station()
    dock = _station(tradedock_tier="A")
    assert not is_tradedock(port)
    assert is_tradedock(dock)
    assert spreads_for(port) == (SELL_SPREAD, BUY_SPREAD)
    assert spreads_for(dock) == (TRADEDOCK_SELL_SPREAD, TRADEDOCK_BUY_SPREAD)
    assert transaction_fee_rate(port) == pytest.approx(0.02)  # Class-0 canon 2%
    assert transaction_fee_rate(dock) == 0.0
    assert inventory_capacity_for(port, 100) == 100
    assert inventory_capacity_for(dock, 100) == 1000


def test_bulk_discount_ladder():
    assert tradedock_bulk_discount_fraction(999) == 0.0
    assert tradedock_bulk_discount_fraction(1000) == pytest.approx(0.05)
    assert tradedock_bulk_discount_fraction(2500) == pytest.approx(0.10)
    assert tradedock_bulk_discount_fraction(4000) == pytest.approx(0.20)
    assert tradedock_bulk_discount_fraction(9999) == pytest.approx(0.20)


def test_tradedock_dynamic_price_uses_premium_spreads():
    commodities = {
        "equipment": {
            "quantity": 50,
            "capacity": 100,
            "base_price": 100,
            "buys": True,
            "sells": True,
        }
    }
    dock = _station(tradedock_tier="A", commodities=commodities)
    port = _station(commodities=commodities)
    svc = TradingService(None)
    dock_sell = svc.calculate_dynamic_price(dock, "equipment", "sell")
    port_sell = svc.calculate_dynamic_price(port, "equipment", "sell")
    dock_buy = svc.calculate_dynamic_price(dock, "equipment", "buy")
    port_buy = svc.calculate_dynamic_price(port, "equipment", "buy")
    # Midpoint at 50% stock ≈ base 100; TradeDock sell cheaper for player,
    # buy pays player more (after commodity-band clamp).
    assert dock_sell < port_sell
    assert dock_buy > port_buy
    assert spreads_for(dock)[0] < spreads_for(port)[0]
    assert spreads_for(dock)[1] > spreads_for(port)[1]


def test_buy_totals_bulk_and_zero_fee_on_tradedock():
    # 4000 units @ 100 → 20% bulk → unit 80 → cost 320000; fee 0
    totals = compute_buy_totals(
        100, 4000, 0.0, transaction_fee_rate=0.0, bulk_discount=0.20
    )
    assert totals["unit_price"] == 80
    assert totals["total_cost"] == 320_000
    assert totals["fee_amount"] == 0
    assert totals["total_with_tax"] == 320_000

    # Ordinary port: no bulk, 2% fee (matches STANDARD_TRANSACTION_FEE)
    ordinary = compute_buy_totals(100, 100, 0.0, transaction_fee_rate=0.02)
    assert ordinary["total_cost"] == 10_000
    assert ordinary["fee_amount"] == 200
    assert ordinary["total_with_tax"] == 10_200


def test_standard_transaction_fee_constant_matches_canon():
    from src.services.trading_service import STANDARD_TRANSACTION_FEE

    assert STANDARD_TRANSACTION_FEE == pytest.approx(0.02)
