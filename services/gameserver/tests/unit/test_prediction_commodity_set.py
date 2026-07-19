"""Unit coverage for MarketPredictionEngine.COMMODITIES
(WO-ARCH-RES-2H-RUNTIME-VOCAB).

Pure Python — no DB. The class attribute is evaluated at module import, so
this pins it as a pure derivation of commodity_economy.COMMODITY_BASE_PRICES
rather than a hand-kept literal that had drifted (missing precious_metals).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.services.market_prediction_engine import MarketPredictionEngine


@pytest.mark.unit
class TestCommoditySetDerivation:
    def test_commodities_equals_commodity_base_prices_keys(self):
        assert MarketPredictionEngine.COMMODITIES == list(COMMODITY_BASE_PRICES.keys())

    def test_commodities_includes_precious_metals(self):
        """Provisional inclusion — auto-tracks Max's pending precious_metals
        ruling (see DECISIONS); today COMMODITY_BASE_PRICES carries it."""
        assert "precious_metals" in MarketPredictionEngine.COMMODITIES

    def test_no_literal_commodity_list_remains_in_source(self):
        import src.services.market_prediction_engine as module
        source = Path(inspect.getfile(module)).read_text()
        # The old hardcoded 8-item literal opened with this exact line;
        # its replacement derives from COMMODITY_BASE_PRICES instead.
        assert 'COMMODITIES = [\n        "ore", "organics", "equipment", "fuel",' not in source
        assert "COMMODITIES = list(COMMODITY_BASE_PRICES)" in source

    def test_engine_instantiates_without_db(self):
        """Confirms the derivation is import-time-safe: no DB/session
        required to build the class or an instance."""
        engine = MarketPredictionEngine()
        assert engine.COMMODITIES == list(COMMODITY_BASE_PRICES.keys())
