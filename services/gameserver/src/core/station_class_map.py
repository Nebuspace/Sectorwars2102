"""Station-class trade-pattern finalization (pure, ORM-free).

Single source of truth for the per-class commodity trading patterns and the
flag/stock finalization that turns a freshly-built 9-commodity dict into a
playable market. The logic was lifted verbatim from the three Station
methods (``get_trading_pattern`` / ``update_commodity_trading_flags`` /
``update_commodity_stock_levels`` in ``src/models/station.py``) so the bang
translator can run the exact same finalization on plain dicts *before* any
ORM row exists. The Station methods now delegate here — change the rules in
this module only.

Determinism contract: callers that need reproducible output (the bang
translator) pass a seeded :class:`random.Random`; callers that want the
legacy behaviour (admin port-editing routes via the Station methods) pass an
unseeded one. Iteration order over the commodities dict is insertion order,
so identical input dicts + identical seeds yield identical output.
"""
from __future__ import annotations

import copy
import random
from typing import Any, Dict, List

from src.models.station import StationClass

#: What each station class buys/sells. Lifted verbatim from
#: ``Station.get_trading_pattern``. Note that some entries reference
#: commodities that are not on the 9-commodity wire (``special_goods``);
#: those are silently skipped by the flag pass, matching the legacy
#: behaviour.
#:
#: Class 11 (Premium Tech Specialist) per
#: ``FEATURES/economy/trading.md#class-11-premium-tech-specialist``: it BUYS
#: AND SELLS only ``exotic_technology`` and ``luxury_goods`` (premium prices
#: both directions — the +25% multiplier itself lives in
#: ``services/trading_service.py``). The legacy ``advanced_components``
#: commodity is not in ``COMMODITY_PRICE_RANGES`` and is dropped from the
#: catalog, so the previous ``sells: [advanced_components]`` was inert (a
#: Class 11 station could buy exotic_technology but had nothing to sell).
CLASS_TRADE_PATTERNS: Dict[StationClass, Dict[str, List[str]]] = {
    StationClass.CLASS_0: {"buys": ["special_goods"], "sells": ["special_goods", "colonists"]},
    StationClass.CLASS_1: {"buys": ["ore"], "sells": ["organics", "equipment"]},
    StationClass.CLASS_2: {"buys": ["organics"], "sells": ["ore", "equipment"]},
    StationClass.CLASS_3: {"buys": ["equipment"], "sells": ["ore", "organics"]},
    StationClass.CLASS_4: {"buys": ["exotic_technology"], "sells": ["ore", "organics", "equipment", "fuel"]},
    StationClass.CLASS_5: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": ["luxury_goods"]},
    StationClass.CLASS_6: {"buys": ["ore", "organics"], "sells": ["equipment", "fuel"]},
    StationClass.CLASS_7: {"buys": ["equipment", "fuel"], "sells": ["ore", "organics"]},
    StationClass.CLASS_8: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": []},
    StationClass.CLASS_9: {"buys": [], "sells": ["ore", "organics", "equipment", "fuel"]},
    StationClass.CLASS_10: {"buys": ["gourmet_food"], "sells": ["luxury_goods", "exotic_technology"]},
    StationClass.CLASS_11: {
        "buys": ["exotic_technology", "luxury_goods"],
        "sells": ["exotic_technology", "luxury_goods"],
    },
}

#: Class-level transaction premium multipliers applied AFTER the supply/demand
#: spread, before the canon price clamp (see
#: ``services/trading_service.py:calculate_dynamic_price``). Source of truth
#: for the canon design targets in ``FEATURES/economy/trading.md``:
#:   - Class 8 (Black Hole): buys at +20% (pays players more)
#:   - Class 9 (Nova): sells at +25% (charges players more)
#:   - Class 11 (Premium Tech Specialist): buys AND sells at +25%, both
#:     directions, on its two commodities (exotic_technology, luxury_goods).
#: Keys are (station_class, transaction_type) where transaction_type is
#: "buy" (station buys from player) or "sell" (station sells to player).
CLASS_PREMIUM_MULTIPLIERS: Dict["tuple[StationClass, str]", float] = {
    (StationClass.CLASS_8, "buy"): 1.20,
    (StationClass.CLASS_9, "sell"): 1.25,
    (StationClass.CLASS_11, "buy"): 1.25,
    (StationClass.CLASS_11, "sell"): 1.25,
}


def get_class_premium(station_class: StationClass, transaction_type: str) -> float:
    """Return the station-class price premium multiplier for a transaction.

    ``transaction_type`` is ``"buy"`` (station buys from player) or
    ``"sell"`` (station sells to player). Returns ``1.0`` (no premium) for
    any class/direction not in :data:`CLASS_PREMIUM_MULTIPLIERS`.

    Unlike the one-directional Class 8/9 patterns, Class 11 buys AND sells
    the same two commodities, so callers must NOT gate the premium on an
    EXCLUSIVE trade flag (``buys and not sells``) for Class 11 — gate on the
    presence of the matching flag for the transaction direction instead.
    """
    return CLASS_PREMIUM_MULTIPLIERS.get((station_class, transaction_type), 1.0)


def get_class_pattern(station_class: StationClass) -> Dict[str, List[str]]:
    """Return the buys/sells pattern for ``station_class``.

    Unknown classes fall back to a fully-inert pattern, mirroring
    ``Station.get_trading_pattern``.
    """
    return CLASS_TRADE_PATTERNS.get(station_class, {"buys": [], "sells": []})


def apply_trading_flags(
    commodities: Dict[str, Dict[str, Any]], station_class: StationClass
) -> Dict[str, Dict[str, Any]]:
    """Set per-commodity buys/sells flags from the class pattern.

    Mutates ``commodities`` in place (and returns it) — replicates
    ``Station.update_commodity_trading_flags``. The class pattern fully
    OVERRIDES any pre-existing flags.
    """
    pattern = get_class_pattern(station_class)

    # Reset all flags
    for commodity in commodities:
        commodities[commodity]["buys"] = False
        commodities[commodity]["sells"] = False

    # Set flags based on trading pattern
    for commodity in pattern.get("buys", []):
        if commodity in commodities:
            commodities[commodity]["buys"] = True

    for commodity in pattern.get("sells", []):
        if commodity in commodities:
            commodities[commodity]["sells"] = True

    return commodities


def apply_stock_levels(
    commodities: Dict[str, Dict[str, Any]],
    station_class: StationClass,
    rng: random.Random,
) -> Dict[str, Dict[str, Any]]:
    """Set per-commodity stock, production rate, and current price.

    Mutates ``commodities`` in place (and returns it) — replicates
    ``Station.update_commodity_stock_levels`` with the module-level
    ``random`` swapped for the caller-supplied ``rng``.
    """
    pattern = get_class_pattern(station_class)
    is_premium_seller = station_class == StationClass.CLASS_9  # Nova
    is_premium_buyer = station_class == StationClass.CLASS_8   # Black Hole
    is_distribution = station_class == StationClass.CLASS_4    # Distribution Center
    is_collection = station_class == StationClass.CLASS_5      # Collection Hub

    for commodity_name, commodity_data in commodities.items():
        base_capacity = commodity_data.get("capacity", 1000)

        # Determine stock level based on port's role with this commodity
        if commodity_name in pattern.get("sells", []):
            # Station sells this commodity - needs high stock
            if is_premium_seller:
                # Premium sellers have maximum stock
                stock_level = int(base_capacity * rng.uniform(0.8, 1.0))
                production_rate = commodity_data.get("production_rate", 50) * 2
            elif is_distribution:
                # Distribution centers have very high stock for selling
                stock_level = int(base_capacity * rng.uniform(0.7, 0.9))
                production_rate = commodity_data.get("production_rate", 50) * 1.5
            else:
                # Regular sellers have good stock
                stock_level = int(base_capacity * rng.uniform(0.4, 0.7))
                production_rate = commodity_data.get("production_rate", 50)

        elif commodity_name in pattern.get("buys", []):
            # Station buys this commodity - needs low stock, high capacity
            if is_premium_buyer or is_collection:
                # Premium buyers and collection hubs have minimal stock, maximum capacity
                stock_level = int(base_capacity * rng.uniform(0.05, 0.15))
                production_rate = 0  # They don't produce, they collect
            else:
                # Regular buyers have low stock
                stock_level = int(base_capacity * rng.uniform(0.1, 0.3))
                production_rate = 0
        else:
            # Station doesn't trade this commodity - minimal stock
            stock_level = int(base_capacity * rng.uniform(0.1, 0.25))
            production_rate = commodity_data.get("production_rate", 10)

        # Ensure minimum stock of 1 for all commodities
        stock_level = max(1, stock_level)

        # Update the commodity data
        commodities[commodity_name]["quantity"] = stock_level
        commodities[commodity_name]["production_rate"] = production_rate

        # Adjust pricing for premium ports
        base_price = commodity_data.get("base_price", 50)
        if is_premium_seller and commodity_name in pattern.get("sells", []):
            # Premium sellers charge less (better deals for players)
            commodities[commodity_name]["current_price"] = int(base_price * 0.8)
        elif is_premium_buyer and commodity_name in pattern.get("buys", []):
            # Premium buyers pay more (better deals for players)
            commodities[commodity_name]["current_price"] = int(base_price * 1.3)
        else:
            commodities[commodity_name]["current_price"] = base_price

    return commodities


def apply_class_pattern(
    commodities: Dict[str, Dict[str, Any]],
    station_class: StationClass,
    rng: random.Random,
) -> Dict[str, Dict[str, Any]]:
    """Finalize a plain commodities dict against its class trade pattern.

    Pure: deep-copies the input, then runs the flag pass followed by the
    stock pass (the combined behaviour of the two legacy Station methods)
    and returns the new dict. The input is never mutated.
    """
    out = copy.deepcopy(commodities)
    apply_trading_flags(out, station_class)
    apply_stock_levels(out, station_class, rng)
    return out
