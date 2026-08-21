"""LEG-398 — tick_production thin-floor dial-down.

Passive regen must not fully substitute for visible supply delivery
(npc-traders.md § Restock by delivery). Pins floor cap + rate scale.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services import trading_service
from src.services.trading_service import (
    TICK_PRODUCTION_RATE_SCALE,
    TICK_PRODUCTION_THIN_FLOOR_RATIO,
    TradingService,
)


def _station(quantity: int, capacity: int = 1000, production_rate: float = 100.0):
    return SimpleNamespace(
        id="st-1",
        commodities={
            "ore": {
                "quantity": quantity,
                "capacity": capacity,
                "production_rate": production_rate,
            }
        },
    )


def test_tick_production_stops_at_thin_floor():
    station = _station(quantity=0, capacity=1000, production_rate=500.0)
    svc = TradingService(MagicMock())
    with patch.object(trading_service, "flag_modified"), patch.object(
        trading_service, "inventory_capacity_for", side_effect=lambda s, c: c,
    ):
        produced = svc.tick_production(station, hours=10.0)

    floor = int(1000 * TICK_PRODUCTION_THIN_FLOOR_RATIO)
    assert station.commodities["ore"]["quantity"] == floor
    assert produced["ore"] == floor
    # Would have filled far past floor at unscaled rate*hours.
    assert floor < int(500.0 * 10.0)


def test_tick_production_noops_when_already_at_or_above_floor():
    floor = int(1000 * TICK_PRODUCTION_THIN_FLOOR_RATIO)
    station = _station(quantity=floor, capacity=1000, production_rate=500.0)
    svc = TradingService(MagicMock())
    with patch.object(trading_service, "flag_modified") as flag, patch.object(
        trading_service, "inventory_capacity_for", side_effect=lambda s, c: c,
    ):
        produced = svc.tick_production(station, hours=10.0)
    assert produced == {}
    assert station.commodities["ore"]["quantity"] == floor
    flag.assert_not_called()


def test_tick_production_rate_scale_slows_fill_below_floor():
    station = _station(quantity=0, capacity=10_000, production_rate=100.0)
    svc = TradingService(MagicMock())
    with patch.object(trading_service, "flag_modified"), patch.object(
        trading_service, "inventory_capacity_for", side_effect=lambda s, c: c,
    ):
        produced = svc.tick_production(station, hours=1.0)

    expected = int(100.0 * TICK_PRODUCTION_RATE_SCALE * 1.0)
    assert expected > 0
    assert produced["ore"] == expected
    assert station.commodities["ore"]["quantity"] == expected


def test_tick_production_still_lifts_from_zero():
    """Floor still prevents permanent zero — Accept criterion."""
    station = _station(quantity=0, capacity=1000, production_rate=40.0)
    svc = TradingService(MagicMock())
    with patch.object(trading_service, "flag_modified"), patch.object(
        trading_service, "inventory_capacity_for", side_effect=lambda s, c: c,
    ):
        produced = svc.tick_production(station, hours=1.0)
    assert produced.get("ore", 0) > 0
    assert station.commodities["ore"]["quantity"] > 0
