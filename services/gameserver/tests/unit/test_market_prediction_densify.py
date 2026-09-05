"""LEG-3880 — market_prediction unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import market_prediction as mp_mod
from src.api.routes.market_prediction import (
    ERR_MARKET_PREDICTION_ANALYSIS_FAILED,
    ERR_MARKET_PREDICTION_BATCH_FAILED,
    ERR_MARKET_PREDICTION_OPPORTUNITIES_FAILED,
    ERR_MARKET_PREDICTION_PREDICT_FAILED,
    commodity_analysis,
    find_opportunities,
    predict_all_prices,
    predict_price,
)


@pytest.mark.asyncio
async def test_predict_price_returns_structured_500():
    secret = "secret-predict-should-not-leak"
    with patch.object(mp_mod._engine, "predict_prices", new=AsyncMock(side_effect=RuntimeError(secret))):
        with pytest.raises(HTTPException) as excinfo:
            await predict_price(commodity="ore", station_id=None, hours_ahead=24, current_player=SimpleNamespace(), db=MagicMock())
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_MARKET_PREDICTION_PREDICT_FAILED, "detail": "Market prediction is temporarily unavailable"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_predict_all_returns_structured_500():
    secret = "secret-batch-predict-should-not-leak"
    with patch.object(mp_mod._engine, "batch_predict", new=AsyncMock(side_effect=RuntimeError(secret))):
        with pytest.raises(HTTPException) as excinfo:
            await predict_all_prices(station_id=None, hours_ahead=24, current_player=SimpleNamespace(), db=MagicMock())
    assert excinfo.value.detail == {"error_code": ERR_MARKET_PREDICTION_BATCH_FAILED, "detail": "Market prediction is temporarily unavailable"}


@pytest.mark.asyncio
async def test_find_opportunities_returns_structured_500():
    secret = "secret-opportunities-should-not-leak"
    with patch.object(mp_mod._engine, "find_opportunities", new=AsyncMock(side_effect=RuntimeError(secret))):
        with pytest.raises(HTTPException) as excinfo:
            await find_opportunities(min_profit_margin=0.1, limit=10, current_player=SimpleNamespace(), db=MagicMock())
    assert excinfo.value.detail == {"error_code": ERR_MARKET_PREDICTION_OPPORTUNITIES_FAILED, "detail": "Market opportunity scan is temporarily unavailable"}


@pytest.mark.asyncio
async def test_commodity_analysis_returns_structured_500():
    secret = "secret-analysis-should-not-leak"
    with patch.object(mp_mod._engine, "get_commodity_analysis", new=AsyncMock(side_effect=RuntimeError(secret))):
        with pytest.raises(HTTPException) as excinfo:
            await commodity_analysis(commodity="ore", station_id=None, current_player=SimpleNamespace(), db=MagicMock())
    assert excinfo.value.detail == {"error_code": ERR_MARKET_PREDICTION_ANALYSIS_FAILED, "detail": "Market analysis is temporarily unavailable"}


def test_market_prediction_http500_catches_are_structured():
    src = Path(mp_mod.__file__).read_text(encoding="utf-8")
    for code in (ERR_MARKET_PREDICTION_PREDICT_FAILED, ERR_MARKET_PREDICTION_BATCH_FAILED, ERR_MARKET_PREDICTION_OPPORTUNITIES_FAILED, ERR_MARKET_PREDICTION_ANALYSIS_FAILED):
        assert code in src
    assert src.count("route_internal_error(") >= 4
