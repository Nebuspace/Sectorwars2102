"""Unit tests for WO-ARCH-RES-2E-NEXUS-PATTERNS.

Pure-catalog / AST tests, no DB — asserts nexus_generation_service's private
shadow trading_patterns dict is gone, the module reads per-class patterns
from src.core.station_class_map.get_class_pattern (the declared SoT), and
the collapse's declared canon behaviour shift lands exactly as specified:

  * CLASS_4 gains buys=['exotic_technology']  (station_class_map.py:45)
  * CLASS_5 gains sells=['luxury_goods']      (station_class_map.py:46)
  * CLASS_11 becomes buys==sells==['exotic_technology','luxury_goods']
    (station_class_map.py:52-55, per FEATURES/economy/trading.md#class-11)

base_commodities (nexus_generation_service.py:601-609) is asserted
byte-identical in shape: exactly 8 keys, no precious_metals, every
base_price sourced from commodity_economy.base_price() via the module's
commodity_base_price alias — never a re-derived literal.
"""
import ast
import inspect
from pathlib import Path

import pytest

import src.services.nexus_generation_service as nexus_generation_service
from src.core.commodity_economy import COMMODITY_BASE_PRICES, base_price as commodity_base_price
from src.core.station_class_map import get_class_pattern
from src.models.station import StationClass

SERVICE_PATH = Path(inspect.getfile(nexus_generation_service))
SERVICE_SOURCE = SERVICE_PATH.read_text()
SERVICE_AST = ast.parse(SERVICE_SOURCE, filename=str(SERVICE_PATH))


def _assign_targets_named(tree, name):
    """Yield every ast.Assign node in `tree` whose target is a Name node
    called `name`, at any nesting depth (module- or method-local)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    yield node


def _extract_base_commodities_dict():
    """Return the ast.Dict node assigned to `base_commodities` inside
    _create_market_prices_for_nexus_stations (there is exactly one)."""
    matches = list(_assign_targets_named(SERVICE_AST, "base_commodities"))
    assert len(matches) == 1, "expected exactly one base_commodities assignment"
    return matches[0].value


def test_no_local_trading_pattern_dict_and_imports_get_class_pattern():
    """The private shadow trading_patterns dict must be gone; the module
    must import the SoT accessor in its place."""
    assert list(_assign_targets_named(SERVICE_AST, "trading_patterns")) == []

    imports_get_class_pattern = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "src.core.station_class_map"
        and any(alias.name == "get_class_pattern" for alias in node.names)
        for node in ast.walk(SERVICE_AST)
    )
    assert imports_get_class_pattern, (
        "nexus_generation_service must import get_class_pattern from "
        "src.core.station_class_map"
    )


def test_canon_class_4_buys_exotic_technology():
    """station_class_map.py:45 — CLASS_4 gains buys=['exotic_technology']."""
    assert get_class_pattern(StationClass.CLASS_4)["buys"] == ["exotic_technology"]


def test_canon_class_5_sells_luxury_goods():
    """station_class_map.py:46 — CLASS_5 gains sells=['luxury_goods']."""
    assert get_class_pattern(StationClass.CLASS_5)["sells"] == ["luxury_goods"]


def test_canon_class_11_buys_and_sells_match():
    """station_class_map.py:52-55 / FEATURES/economy/trading.md#class-11 —
    CLASS_11 buys AND sells exotic_technology + luxury_goods, both
    directions."""
    pattern = get_class_pattern(StationClass.CLASS_11)
    assert pattern["buys"] == ["exotic_technology", "luxury_goods"]
    assert pattern["sells"] == ["exotic_technology", "luxury_goods"]


def test_pattern_variable_bound_via_get_class_pattern_call():
    """The `pattern` variable consumed inside the per-station loop is bound
    directly to get_class_pattern(station.station_class) — not a local
    dict/lookup of any kind — so at runtime the pattern used for EVERY
    StationClass equals get_class_pattern(cls), the Accept criterion."""
    matches = [
        node for node in _assign_targets_named(SERVICE_AST, "pattern")
        if isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "get_class_pattern"
    ]
    assert len(matches) == 1, "expected exactly one `pattern = get_class_pattern(...)` binding"

    call = matches[0].value
    assert len(call.args) == 1
    arg = call.args[0]
    assert isinstance(arg, ast.Attribute) and arg.attr == "station_class", (
        "get_class_pattern must be called with station.station_class"
    )


def test_base_commodities_has_exactly_eight_keys_no_precious_metals():
    base_dict = _extract_base_commodities_dict()
    keys = [k.value for k in base_dict.keys]
    assert len(keys) == 8
    assert "precious_metals" not in keys


def test_base_commodities_base_price_traces_to_commodity_economy():
    """Every base_commodities entry's base_price is produced by a call to
    the SoT function (commodity_base_price, the module's alias for
    src.core.commodity_economy.base_price) applied to that entry's own key —
    never a re-derived literal."""
    base_dict = _extract_base_commodities_dict()
    for key_node, value_node in zip(base_dict.keys, base_dict.values):
        slug = key_node.value
        assert isinstance(value_node, ast.Dict)
        entry_keys = [k.value for k in value_node.keys]
        assert "base_price" in entry_keys

        base_price_expr = value_node.values[entry_keys.index("base_price")]
        assert isinstance(base_price_expr, ast.Call)
        assert base_price_expr.func.id == "commodity_base_price"
        assert len(base_price_expr.args) == 1
        assert isinstance(base_price_expr.args[0], ast.Constant)
        assert base_price_expr.args[0].value == slug

        # And the SoT function itself resolves to the canon price for
        # every commodity actually referenced.
        assert commodity_base_price(slug) == COMMODITY_BASE_PRICES[slug]["base"]


def test_special_goods_class_0_resolves_without_raising():
    """CLASS_0's 'special_goods' pattern entries have no base_commodities
    match and must keep being silently skipped (station_class_map.py
    docstring :27-30), not raise. The production loop iterates over
    base_commodities.items() and checks membership in the pattern's
    buys/sells lists (never indexes a dict by a pattern-derived name), so
    this is safe by construction — assert that shape holds and exercise it."""
    base_dict = _extract_base_commodities_dict()
    base_commodity_keys = {k.value for k in base_dict.keys}

    pattern = get_class_pattern(StationClass.CLASS_0)
    assert pattern == {"buys": ["special_goods"], "sells": ["special_goods", "colonists"]}
    assert "special_goods" not in base_commodity_keys
    assert "colonists" in base_commodity_keys

    buys_list = pattern.get("buys", [])
    sells_list = pattern.get("sells", [])
    resolved = {}
    try:
        for commodity_name in base_commodity_keys:
            is_buy = commodity_name in buys_list
            is_sell = commodity_name in sells_list
            if not is_buy and not is_sell:
                continue
            resolved[commodity_name] = {"buy": is_buy, "sell": is_sell}
    except KeyError:
        pytest.fail("resolving CLASS_0's pattern against base_commodities raised KeyError")

    # colonists is CLASS_0's only base_commodities-resolvable sell; special_goods
    # never appears (it has no match, matching the documented silent skip).
    assert resolved == {"colonists": {"buy": False, "sell": True}}
