"""LEG-3609 — GET /admin/analytics/predictions wired to MarketPredictionEngine."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes import admin_comprehensive as admin_route
from src.services.market_prediction_engine import PricePrediction


def _sample_prediction(commodity: str = "ore") -> PricePrediction:
    return PricePrediction(
        commodity=commodity,
        station_id="global",
        current_price=100.0,
        predicted_price=110.0,
        price_change_pct=10.0,
        trend="rising",
        confidence=0.75,
        volatility=0.05,
        lower_bound=95.0,
        upper_bound=115.0,
        prediction_horizon_hours=24,
        factors=["uptrend"],
        timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_analytics_predictions_batch_returns_engine_rows(monkeypatch):
    engine = MagicMock()
    engine.batch_predict = AsyncMock(return_value={"ore": _sample_prediction()})
    monkeypatch.setattr(admin_route, "_market_prediction_engine", engine)

    admin = SimpleNamespace(username="ops")
    db = MagicMock()

    payload = await admin_route.get_analytics_predictions(
        timeframe="1d",
        resource=None,
        station_id=None,
        hours_ahead=None,
        current_admin=admin,
        db=db,
    )

    engine.batch_predict.assert_awaited_once_with(db, station_id=None, hours_ahead=24)
    assert payload["count"] == 1
    assert payload["predictions"][0]["commodity"] == "ore"


@pytest.mark.asyncio
async def test_analytics_predictions_resource_filter_uses_single_predict(monkeypatch):
    engine = MagicMock()
    engine.predict_prices = AsyncMock(return_value=_sample_prediction("fuel"))
    monkeypatch.setattr(admin_route, "_market_prediction_engine", engine)

    admin = SimpleNamespace(username="ops")
    db = MagicMock()

    payload = await admin_route.get_analytics_predictions(
        timeframe="4h",
        resource="fuel",
        station_id="station-1",
        hours_ahead=6,
        current_admin=admin,
        db=db,
    )

    engine.predict_prices.assert_awaited_once_with(
        db, commodity="fuel", station_id="station-1", hours_ahead=6
    )
    assert payload["resource"] == "fuel"
    assert payload["count"] == 1


@pytest.mark.asyncio
async def test_analytics_predictions_empty_when_no_market_data(monkeypatch):
    engine = MagicMock()
    engine.batch_predict = AsyncMock(return_value={})
    monkeypatch.setattr(admin_route, "_market_prediction_engine", engine)

    admin = SimpleNamespace(username="ops")
    db = MagicMock()

    payload = await admin_route.get_analytics_predictions(
        timeframe="1h",
        resource=None,
        station_id=None,
        hours_ahead=None,
        current_admin=admin,
        db=db,
    )

    assert payload["predictions"] == []
    assert payload["count"] == 0


@pytest.mark.asyncio
async def test_ai_predictions_legacy_row_shape(monkeypatch):
    engine = MagicMock()
    engine.batch_predict = AsyncMock(return_value={"ore": _sample_prediction()})
    monkeypatch.setattr(admin_route, "_market_prediction_engine", engine)

    admin = SimpleNamespace(username="ops")
    db = MagicMock()

    rows = await admin_route.get_ai_predictions(
        timeframe="1h",
        resource=None,
        station_id=None,
        hours_ahead=None,
        current_admin=admin,
        db=db,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["resourceId"] == "ore"
    assert row["confidence"] == 75.0
