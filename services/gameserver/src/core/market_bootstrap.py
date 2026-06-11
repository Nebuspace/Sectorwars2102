"""Shared MarketPrice bootstrap pricing (pure spread logic).

The trading endpoint reads from the ``market_prices`` table, not the
``Station.commodities`` JSONB — a station without MarketPrice rows is
invisible to trade. This module is the single source of truth for turning
a finalized commodities dict into the initial MarketPrice rows.

The spread logic was lifted verbatim from ``backfill_market_prices.py``
(itself lifted from the deleted ``GalaxyGenerator.backfill_market_prices``,
galaxy_service.py:909-976) so the bang translator and the repair CLI price
stations identically:

* both directions: buy 0.85× / sell 1.15× of current price
* buy-only:        buy 1.1×  / sell 1.5×  (station pays a premium)
* sell-only:       buy 0.5×  / sell 0.9×  (station charges competitively)
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from src.models.market_transaction import MarketPrice


def build_market_prices(
    station_id: uuid.UUID,
    commodities: Dict[str, Dict[str, Any]],
) -> List[MarketPrice]:
    """Return unsaved MarketPrice rows for every commodity the station trades.

    Pure: no session access — callers ``session.add()`` the returned rows.
    Commodities with neither ``buys`` nor ``sells`` set are skipped, so a
    fully-inert dict yields an empty list.
    """
    rows: List[MarketPrice] = []

    for commodity_name, commodity_data in (commodities or {}).items():
        buys = commodity_data.get("buys", False)
        sells = commodity_data.get("sells", False)

        # Only create market prices for commodities the station trades
        if not buys and not sells:
            continue

        quantity = commodity_data.get("quantity", 0)
        base_price = commodity_data.get("base_price", 10)
        current_price = commodity_data.get("current_price", base_price)

        # Calculate buy/sell prices with a spread
        if buys and sells:
            buy_price = int(current_price * 0.85)
            sell_price = int(current_price * 1.15)
        elif buys:
            # Station only buys - willing to pay more
            buy_price = int(current_price * 1.1)
            sell_price = int(current_price * 1.5)
        else:
            # Station only sells - charges competitive price
            buy_price = int(current_price * 0.5)
            sell_price = int(current_price * 0.9)

        rows.append(
            MarketPrice(
                station_id=station_id,
                commodity=commodity_name,
                quantity=quantity,
                buy_price=buy_price,
                sell_price=sell_price,
                supply_level=1.0,
                demand_level=1.0,
            )
        )

    return rows
