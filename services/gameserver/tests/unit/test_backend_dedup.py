"""Unit tests for WO-ARCH-RES-2-BACKEND-DEDUP.

Pure-catalog tests, no DB — asserts every collapsed hardcoded-price site now
reads its numbers from src.core.commodity_economy.COMMODITY_BASE_PRICES
(the WO-Y / ADR-0082 single source of truth) rather than a private literal
copy, and that the collapse is behaviour-preserving (produces the exact same
values the old hardcoded dicts held).

Sites covered (see WO-ARCH-RES-2-BACKEND-DEDUP for the full site list):
  * models/station.py — Station.commodities JSONB column default
  * services/bang_import_service.py — _COMMODITY_DEFAULTS
  * repair_tradedocks.py (gameserver root) — COMMODITY_DEFAULTS

Sites intentionally NOT touched (flagged to lead/orchestrator, not forced —
see STATUS report): core/station_class_map.py (CLASS_TRADE_PATTERNS carries
buy/sell class membership, not price data) and services/citadel_service.py
CITADEL_LEVELS (resource_cost carries upgrade-material quantities, not price
data — the file's genuine price duplicate, COMMODITY_CREDIT_VALUE, was
already collapsed under WO-Y).
"""
import importlib.util
from pathlib import Path

import pytest

from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.services.bang_import_service import _COMMODITY_DEFAULTS
from src.models.station import Station

PRICED_COMMODITIES = list(COMMODITY_BASE_PRICES.keys())

GAMESERVER_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_repair_tradedocks():
    """Load repair_tradedocks.py by path (it lives outside src/, at the
    gameserver root, and is a standalone repair script — not a package
    module). Its DB-touching logic is guarded by ``if __name__ ==
    "__main__"``, so importing it here is side-effect free."""
    spec = importlib.util.spec_from_file_location(
        "repair_tradedocks", GAMESERVER_ROOT / "repair_tradedocks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("commodity_key", PRICED_COMMODITIES)
def test_station_commodities_default_matches_commodity_economy(commodity_key):
    """Station.commodities column default base_price/current_price trace 1:1
    to commodity_economy.COMMODITY_BASE_PRICES — no re-derived/invented
    literal survives the WO-ARCH-RES-2 collapse."""
    default = Station.__table__.c.commodities.default.arg
    expected = COMMODITY_BASE_PRICES[commodity_key]["base"]
    assert default[commodity_key]["base_price"] == expected
    assert default[commodity_key]["current_price"] == expected


@pytest.mark.parametrize("commodity_key", PRICED_COMMODITIES)
def test_bang_import_defaults_match_commodity_economy(commodity_key):
    """bang_import_service._COMMODITY_DEFAULTS base_price traces 1:1 to
    commodity_economy.COMMODITY_BASE_PRICES."""
    expected = COMMODITY_BASE_PRICES[commodity_key]["base"]
    assert _COMMODITY_DEFAULTS[commodity_key]["base_price"] == expected


@pytest.mark.parametrize("commodity_key", PRICED_COMMODITIES)
def test_repair_tradedocks_defaults_match_commodity_economy(commodity_key):
    """repair_tradedocks.COMMODITY_DEFAULTS base_price traces 1:1 to
    commodity_economy.COMMODITY_BASE_PRICES."""
    repair_tradedocks = _load_repair_tradedocks()
    expected = COMMODITY_BASE_PRICES[commodity_key]["base"]
    assert repair_tradedocks.COMMODITY_DEFAULTS[commodity_key]["base_price"] == expected


def test_all_three_sites_agree_with_each_other():
    """Cross-check: the three collapsed sites can no longer drift from one
    another (the actual regression WO-ARCH-RES-2 exists to prevent)."""
    station_default = Station.__table__.c.commodities.default.arg
    repair_tradedocks = _load_repair_tradedocks()

    for commodity_key in PRICED_COMMODITIES:
        station_price = station_default[commodity_key]["base_price"]
        bang_price = _COMMODITY_DEFAULTS[commodity_key]["base_price"]
        repair_price = repair_tradedocks.COMMODITY_DEFAULTS[commodity_key]["base_price"]
        assert station_price == bang_price == repair_price
