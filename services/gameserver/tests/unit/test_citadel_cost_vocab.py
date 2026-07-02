"""Unit tests for WO-ARCH-RES-2I-D (citadel resource_cost vocabulary guard).

Pure-catalog tests, no DB — asserts the import-time validation guard on
CITADEL_LEVELS resource_cost keys: every key resolves via
canonical_commodity() into COMMODITY_BASE_PRICES, and a bad slug raises on
revalidation. CITADEL_LEVELS numbers themselves stay byte-identical
(VALIDATION ONLY — no balance change, CRT-4 machinery untouched).
"""

import pytest

from src.core.commodity_economy import COMMODITY_BASE_PRICES, canonical_commodity
from src.services.citadel_service import (
    CITADEL_LEVELS,
    _validate_citadel_resource_cost_vocab,
)


def test_module_imports_cleanly_all_resource_cost_keys_resolve():
    """The module already imported cleanly (this test running proves the
    import-time guard passed) — re-assert directly for every level too."""
    for _level, spec in CITADEL_LEVELS.items():
        for slug in spec["resource_cost"]:
            assert canonical_commodity(slug) in COMMODITY_BASE_PRICES


def test_citadel_levels_resource_cost_numbers_byte_identical():
    """VALIDATION ONLY — zero numeric change to CITADEL_LEVELS."""
    assert CITADEL_LEVELS[1]["resource_cost"] == {}
    assert CITADEL_LEVELS[2]["resource_cost"] == {"fuel_ore": 500, "equipment": 200}
    assert CITADEL_LEVELS[3]["resource_cost"] == {
        "fuel_ore": 1500, "organics": 500, "equipment": 800,
    }
    assert CITADEL_LEVELS[4]["resource_cost"] == {
        "fuel_ore": 5000, "organics": 2000, "equipment": 3000,
    }
    assert CITADEL_LEVELS[5]["resource_cost"] == {
        "fuel_ore": 15000, "organics": 8000, "equipment": 10000,
    }


def test_bad_slug_raises_on_revalidation(monkeypatch):
    """A non-resolving resource_cost slug must raise — proves the guard is
    load-bearing, not a no-op."""
    bad_levels = {1: {"resource_cost": {"not_a_real_commodity": 100}}}
    monkeypatch.setattr("src.services.citadel_service.CITADEL_LEVELS", bad_levels)
    with pytest.raises(AssertionError):
        _validate_citadel_resource_cost_vocab()
