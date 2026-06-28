"""Unit coverage for ``src.core.market_bootstrap.build_market_prices``.

The helper is shared by the bang translator (_apply_region) and the
backfill_market_prices.py repair CLI; these tests pin the spread contract
lifted from the legacy GalaxyGenerator.backfill_market_prices.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest

from src.core.market_bootstrap import build_market_prices

STATION_ID = uuid.uuid4()


def _commodity(
    *, buys: bool, sells: bool, quantity: int = 100, current_price: int = 100
) -> Dict[str, Any]:
    return {
        "quantity": quantity,
        "capacity": 1000,
        "base_price": current_price,
        "current_price": current_price,
        "production_rate": 50,
        "price_variance": 20,
        "buys": buys,
        "sells": sells,
    }


@pytest.mark.unit
class TestBuildMarketPrices:
    """Rows only for traded commodities; spreads match the legacy backfill."""

    def test_rows_only_for_traded_commodities(self) -> None:
        commodities = {
            "ore": _commodity(buys=True, sells=False),
            "organics": _commodity(buys=False, sells=True),
            "equipment": _commodity(buys=True, sells=True),
            "fuel": _commodity(buys=False, sells=False),
            "luxury_goods": _commodity(buys=False, sells=False),
        }
        rows = build_market_prices(STATION_ID, commodities)
        assert {r.commodity for r in rows} == {"ore", "organics", "equipment"}
        for row in rows:
            assert row.station_id == STATION_ID

    def test_both_directions_spread(self) -> None:
        rows = build_market_prices(
            STATION_ID, {"equipment": _commodity(buys=True, sells=True, current_price=100)}
        )
        (row,) = rows
        assert row.buy_price == 85    # int(100 * 0.85)
        # int() truncates and 1.15 is not exactly representable in binary
        # floating point: int(100 * 1.15) == 114. Pinned because the helper
        # must replicate the legacy backfill byte-for-byte.
        assert row.sell_price == 114
        assert row.sell_price >= row.buy_price

    def test_buy_only_spread(self) -> None:
        (row,) = build_market_prices(
            STATION_ID, {"ore": _commodity(buys=True, sells=False, current_price=100)}
        )
        assert row.buy_price == 110   # 1.1x — station pays a premium
        assert row.sell_price == 150  # 1.5x

    def test_sell_only_spread(self) -> None:
        (row,) = build_market_prices(
            STATION_ID, {"fuel": _commodity(buys=False, sells=True, current_price=100)}
        )
        assert row.buy_price == 50    # 0.5x
        assert row.sell_price == 90   # 0.9x — competitive

    def test_quantity_and_levels_carried_onto_row(self) -> None:
        (row,) = build_market_prices(
            STATION_ID, {"ore": _commodity(buys=True, sells=False, quantity=321)}
        )
        assert row.quantity == 321
        assert row.supply_level == 1.0
        assert row.demand_level == 1.0

    def test_inert_or_empty_commodities_yield_no_rows(self) -> None:
        assert build_market_prices(STATION_ID, {}) == []
        assert build_market_prices(STATION_ID, None) == []  # type: ignore[arg-type]
        inert = {"ore": _commodity(buys=False, sells=False)}
        assert build_market_prices(STATION_ID, inert) == []
