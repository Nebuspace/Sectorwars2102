"""LEG-3872 — admin_economy unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_economy as ae_mod
from src.api.routes.admin_economy import (
    ERR_ADMIN_ECONOMY_DASHBOARD_FAILED,
    ERR_ADMIN_ECONOMY_INTERVENTION_FAILED,
    ERR_ADMIN_ECONOMY_MARKET_DATA_FAILED,
    ERR_ADMIN_ECONOMY_METRICS_FAILED,
    ERR_ADMIN_ECONOMY_PRICE_ALERTS_FAILED,
    MarketInterventionRequest,
    get_dashboard_summary,
    get_economic_metrics,
    get_market_data,
    get_price_alerts,
    perform_market_intervention,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-market-data-query-should-not-leak")


@pytest.mark.asyncio
async def test_get_market_data_unexpected_returns_structured_500():
    secret = "secret-market-data-query-should-not-leak"

    with pytest.raises(HTTPException) as excinfo:
        await get_market_data(admin=SimpleNamespace(), db=_BoomDB())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_ECONOMY_MARKET_DATA_FAILED,
        "detail": "Failed to retrieve market data",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_economic_metrics_unexpected_returns_structured_500():
    secret = "secret-economic-metrics-should-not-leak"
    db = MagicMock()
    db.query.side_effect = RuntimeError(secret)

    with pytest.raises(HTTPException) as excinfo:
        await get_economic_metrics(admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_ECONOMY_METRICS_FAILED,
        "detail": "Failed to retrieve economic metrics",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_price_alerts_unexpected_returns_structured_500():
    secret = "secret-price-alerts-should-not-leak"

    with patch.object(ae_mod, "EconomyAnalyticsService") as svc_cls:
        svc_cls.return_value.get_price_alerts.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_price_alerts(admin=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_ECONOMY_PRICE_ALERTS_FAILED,
        "detail": "Failed to retrieve price alerts",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_perform_market_intervention_unexpected_returns_structured_500():
    secret = "secret-intervention-should-not-leak"
    request = MarketInterventionRequest(
        intervention_type="reset_market",
        parameters={"resource_type": "ore"},
    )

    with patch.object(ae_mod, "EconomyAnalyticsService") as svc_cls:
        svc_cls.return_value.perform_market_intervention.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await perform_market_intervention(
                request=request,
                admin=SimpleNamespace(id=1),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_ECONOMY_INTERVENTION_FAILED,
        "detail": "Market intervention failed",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_dashboard_summary_unexpected_returns_structured_500():
    secret = "secret-dashboard-summary-should-not-leak"

    with patch.object(ae_mod, "EconomyAnalyticsService") as svc_cls:
        svc_cls.return_value.get_market_data.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_dashboard_summary(admin=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_ECONOMY_DASHBOARD_FAILED,
        "detail": "Failed to generate dashboard summary",
    }
    assert secret not in str(exc.detail)


def test_admin_economy_http500_catches_are_structured():
    """LEG-3872 — static pin: economy admin 500 catch paths emit error_code + detail."""
    src = Path(ae_mod.__file__).read_text(encoding="utf-8")
    for code in (
        ERR_ADMIN_ECONOMY_MARKET_DATA_FAILED,
        ERR_ADMIN_ECONOMY_METRICS_FAILED,
        ERR_ADMIN_ECONOMY_PRICE_ALERTS_FAILED,
        ERR_ADMIN_ECONOMY_INTERVENTION_FAILED,
        ERR_ADMIN_ECONOMY_DASHBOARD_FAILED,
    ):
        assert code in src
    assert "route_internal_error" in src
    assert src.count("route_internal_error(") >= 5
    assert 'detail="Failed to retrieve market data"' not in src
    assert 'detail="Failed to retrieve economic metrics"' not in src
    assert 'detail="Failed to retrieve price alerts"' not in src
    assert 'detail="Market intervention failed"' not in src
    assert 'detail="Failed to generate dashboard summary"' not in src
    assert "Failed to retrieve market data: {str(e)}" not in src
    assert "Failed to retrieve economic metrics: {str(e)}" not in src
    assert "Failed to retrieve price alerts: {str(e)}" not in src
    assert "Market intervention failed: {str(e)}" not in src
    assert "Failed to generate dashboard summary: {str(e)}" not in src
