"""Unit coverage for RealTimeMarketService's default-snapshot vocabulary fix
(WO-ARCH-RES-2H-RUNTIME-VOCAB).

Pure Python — no DB, no Redis. Confirms the UPPER_CASE dead default table is
gone, defaults derive from commodity_economy's price ranges (lowercase,
matching every real caller), and the 4 registry resources with no canon
price get an explicit no-data marker instead of an invented number.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.core.commodity_economy import COMMODITY_BASE_PRICES, get_commodity_price_ranges
from src.services.realtime_market_service import RealTimeMarketService


@pytest.fixture
def service() -> RealTimeMarketService:
    return RealTimeMarketService(redis_client=None)


@pytest.mark.unit
class TestDefaultSnapshotVocabulary:
    def test_ore_default_is_range_midpoint_not_hundred(self, service):
        snapshot = service._create_default_snapshot("ore")
        assert snapshot.current_price == 30.0  # (15 + 45) / 2, not the old 100.0 fallback
        assert snapshot.high_24h == 30.0
        assert snapshot.low_24h == 30.0
        assert snapshot.no_market_data is False

    @pytest.mark.parametrize("commodity", list(COMMODITY_BASE_PRICES))
    def test_every_priced_commodity_matches_range_midpoint(self, service, commodity):
        ranges = get_commodity_price_ranges()
        expected = (ranges[commodity]["min"] + ranges[commodity]["max"]) / 2.0
        snapshot = service._create_default_snapshot(commodity)
        assert snapshot.current_price == expected
        assert snapshot.no_market_data is False

    @pytest.mark.parametrize(
        "commodity", ["quantum_shards", "quantum_crystals", "prismatic_ore", "lumen_crystals"]
    )
    def test_unpriced_registry_resources_return_no_data_marker(self, service, commodity):
        snapshot = service._create_default_snapshot(commodity)
        assert snapshot.no_market_data is True
        assert snapshot.current_price == 0.0  # never an invented price

    def test_no_stale_uppercase_default_table_or_ghost_vocab(self):
        import src.services.realtime_market_service as module
        source = Path(inspect.getfile(module)).read_text()
        assert "photonic_crystals" not in source
        for ghost_key in ('"ORE"', '"BASIC_FOOD"', '"TECHNOLOGY"', '"POPULATION"', '"QUANTUM_SHARDS"'):
            assert ghost_key not in source


@pytest.mark.unit
class TestTaxonomyDerivation:
    """valid_commodities/strategic_resources/rare_materials now derive from
    the resource registry's category split instead of a hand-kept literal."""

    def test_valid_commodities_are_the_seven_core_canon_slugs(self, service):
        assert set(service.valid_commodities) == {
            "ore", "organics", "gourmet_food", "fuel",
            "equipment", "exotic_technology", "luxury_goods",
        }

    def test_rare_materials_uses_lumen_crystals_not_photonic_crystals(self, service):
        assert set(service.rare_materials) == {"prismatic_ore", "lumen_crystals"}

    def test_strategic_resources_membership_unchanged(self, service):
        assert set(service.strategic_resources) == {
            "colonists", "quantum_shards", "quantum_crystals", "combat_drones",
        }
