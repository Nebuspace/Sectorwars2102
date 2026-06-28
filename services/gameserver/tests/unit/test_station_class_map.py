"""Unit coverage for ``src.core.station_class_map``.

The module is the single source of truth for class-pattern commodity
finalization, shared by the Station ORM methods and the bang translator.
These tests pin the contract the translator relies on:

* every station class yields at least one actively-traded commodity,
* sold commodities are stocked (quantity >= 1),
* the function is pure and deterministic under a fixed seed.
"""
from __future__ import annotations

import copy
import random
from typing import Any, Dict

import pytest

from src.core.station_class_map import (
    CLASS_TRADE_PATTERNS,
    apply_class_pattern,
    get_class_pattern,
)
from src.models.station import StationClass

#: Mirrors COMMODITY_WIRE_ORDER in bang_import_service (ADR-0062 E-D1).
COMMODITY_KEYS = (
    "ore",
    "organics",
    "equipment",
    "fuel",
    "luxury_goods",
    "gourmet_food",
    "exotic_technology",
    "colonists",
    "precious_metals",
)


def _commodities() -> Dict[str, Dict[str, Any]]:
    """A fully-inert 9-commodity wire dict, shaped like _build_full_commodities({})."""
    return {
        key: {
            "quantity": 0,
            "capacity": 1000,
            "base_price": 50,
            "current_price": 50,
            "production_rate": 50,
            "price_variance": 20,
            "buys": False,
            "sells": False,
        }
        for key in COMMODITY_KEYS
    }


@pytest.mark.unit
class TestApplyClassPattern:
    """apply_class_pattern finalizes a plain dict against the class pattern."""

    @pytest.mark.parametrize("station_class", list(StationClass), ids=lambda c: c.name)
    def test_every_class_yields_active_commodity(
        self, station_class: StationClass
    ) -> None:
        # Every CLASS_TRADE_PATTERNS entry references >=1 real wire key
        # (e.g. CLASS_0 sells colonists, CLASS_11 buys exotic_technology),
        # so no class may come out fully inert.
        pattern = CLASS_TRADE_PATTERNS[station_class]
        real_keys = (set(pattern["buys"]) | set(pattern["sells"])) & set(COMMODITY_KEYS)
        assert real_keys, f"{station_class.name} pattern has no real wire keys"

        result = apply_class_pattern(
            _commodities(), station_class, random.Random(f"seed:{station_class.name}")
        )
        active = [k for k, c in result.items() if c["buys"] or c["sells"]]
        assert len(active) >= 1, f"{station_class.name} produced a fully-inert station"
        assert set(active) == real_keys

    @pytest.mark.parametrize("station_class", list(StationClass), ids=lambda c: c.name)
    def test_sold_commodities_are_stocked(self, station_class: StationClass) -> None:
        result = apply_class_pattern(
            _commodities(), station_class, random.Random(f"seed:{station_class.name}")
        )
        for key, commodity in result.items():
            if commodity["sells"]:
                assert commodity["quantity"] >= 1, (
                    f"{station_class.name} sells {key} with zero stock"
                )

    def test_same_seed_is_deterministic(self) -> None:
        seed = "42:7:Station 7"
        a = apply_class_pattern(_commodities(), StationClass.CLASS_6, random.Random(seed))
        b = apply_class_pattern(_commodities(), StationClass.CLASS_6, random.Random(seed))
        assert a == b

    def test_input_dict_is_not_mutated(self) -> None:
        original = _commodities()
        snapshot = copy.deepcopy(original)
        apply_class_pattern(original, StationClass.CLASS_1, random.Random(1))
        assert original == snapshot

    def test_pattern_overrides_preexisting_flags(self) -> None:
        # Bang's per-commodity B/S flags must be fully OVERRIDDEN by the
        # class pattern (SYSTEMS/bang-import-pipeline §11 / Appendix A).
        commodities = _commodities()
        for c in commodities.values():
            c["buys"] = True
            c["sells"] = True
        result = apply_class_pattern(commodities, StationClass.CLASS_1, random.Random(1))
        # CLASS_1 (Mining Operation): buys ore; sells organics + equipment.
        assert result["ore"]["buys"] is True and result["ore"]["sells"] is False
        assert result["organics"]["sells"] is True and result["organics"]["buys"] is False
        assert result["equipment"]["sells"] is True and result["equipment"]["buys"] is False
        for key in ("fuel", "luxury_goods", "gourmet_food", "exotic_technology",
                    "colonists", "precious_metals"):
            assert result[key]["buys"] is False
            assert result[key]["sells"] is False

    def test_premium_buyer_and_seller_price_adjustments(self) -> None:
        # CLASS_8 (Black Hole) pays 1.3x base on bought commodities;
        # CLASS_9 (Nova) charges 0.8x base on sold commodities.
        black_hole = apply_class_pattern(_commodities(), StationClass.CLASS_8, random.Random(1))
        assert black_hole["ore"]["current_price"] == int(50 * 1.3)
        nova = apply_class_pattern(_commodities(), StationClass.CLASS_9, random.Random(1))
        assert nova["ore"]["current_price"] == int(50 * 0.8)

    def test_unknown_class_pattern_is_inert(self) -> None:
        # get_class_pattern falls back to an empty pattern, mirroring the
        # legacy Station.get_trading_pattern default.
        assert get_class_pattern("not-a-class") == {"buys": [], "sells": []}  # type: ignore[arg-type]
