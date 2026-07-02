"""Unit tests for WO-ARCH-RES-2I-C (illegal-commodities cross-catalog refs).

Pure-catalog tests, no DB — asserts the two legal-market-referencing entries
in ILLEGAL_COMMODITY_CATALOG derive their base_price from
commodity_economy.base_price() (the SoT) rather than a drift-prone literal,
so a future base-price move propagates automatically. The pure seizure-value
literals and the 'never merge' catalog separation stay unchanged.
"""

from src.core.commodity_economy import base_price
from src.core.illegal_commodities import (
    ILLEGAL_COMMODITY_CATALOG,
    IllegalCommodity,
)


def test_stolen_goods_price_derives_from_luxury_goods_sot():
    entry = ILLEGAL_COMMODITY_CATALOG[IllegalCommodity.STOLEN_GOODS]
    assert entry.base_price == base_price("luxury_goods")


def test_restricted_tech_price_derives_from_exotic_technology_sot():
    entry = ILLEGAL_COMMODITY_CATALOG[IllegalCommodity.RESTRICTED_TECH]
    assert entry.base_price == base_price("exotic_technology")


def test_seizure_value_literals_unchanged():
    """WEAPONS/CONTRABAND_SUBSTANCES/SLAVES reference no legal commodity —
    they must stay pure literals, not be pulled into the SoT derivation."""
    assert ILLEGAL_COMMODITY_CATALOG[IllegalCommodity.WEAPONS].base_price == 2000
    assert ILLEGAL_COMMODITY_CATALOG[IllegalCommodity.CONTRABAND_SUBSTANCES].base_price == 1500
    assert ILLEGAL_COMMODITY_CATALOG[IllegalCommodity.SLAVES].base_price == 2500


def test_catalog_stays_separate_from_resource_type():
    """The 'never merge' invariant (module docstring §1.2) — the module
    itself declares no import from models.resource.ResourceType, even after
    the SoT derivation was added."""
    import inspect

    import src.core.illegal_commodities as mod

    source = inspect.getsource(mod)
    assert "from src.models" not in source
    assert "import src.models" not in source
