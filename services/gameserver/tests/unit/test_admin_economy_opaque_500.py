"""LEG-3605 — admin_economy.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3595 teams.py / LEG-3581 audit.py opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_economy as ae_mod
from src.api.routes.admin_economy import (
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
async def test_get_market_data_unexpected_is_opaque_500():
    """Outer get_market_data catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_market_data(admin=SimpleNamespace(), db=_BoomDB())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to retrieve market data"
    assert "secret-market-data-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_economic_metrics_unexpected_is_opaque_500():
    """get_economic_metrics catch must not echo raw Exception text."""
    secret = "secret-economic-metrics-should-not-leak"
    db = MagicMock()
    db.query.side_effect = RuntimeError(secret)

    with pytest.raises(HTTPException) as excinfo:
        await get_economic_metrics(admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to retrieve economic metrics"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_price_alerts_unexpected_is_opaque_500():
    """get_price_alerts catch must not echo raw Exception text."""
    secret = "secret-price-alerts-should-not-leak"

    with patch.object(ae_mod, "EconomyAnalyticsService") as svc_cls:
        svc_cls.return_value.get_price_alerts.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_price_alerts(admin=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to retrieve price alerts"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_perform_market_intervention_unexpected_is_opaque_500():
    """perform_market_intervention catch must not echo raw Exception text."""
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
    assert exc.detail == "Market intervention failed"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_dashboard_summary_unexpected_is_opaque_500():
    """get_dashboard_summary catch must not echo raw Exception text."""
    secret = "secret-dashboard-summary-should-not-leak"

    with patch.object(ae_mod, "EconomyAnalyticsService") as svc_cls:
        svc_cls.return_value.get_market_data.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_dashboard_summary(admin=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to generate dashboard summary"
    assert secret not in str(exc.detail)


def test_admin_economy_http500_catches_have_no_detail_str_e():
    """LEG-3605 — static pin: all five HTTP 500 catch paths stay opaque."""
    src = Path(ae_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to retrieve market data"',
        'detail="Failed to retrieve economic metrics"',
        'detail="Failed to retrieve price alerts"',
        'detail="Market intervention failed"',
        'detail="Failed to generate dashboard summary"',
    ):
        assert stable in src
    assert "Failed to retrieve market data: {str(e)}" not in src
    assert "Failed to retrieve economic metrics: {str(e)}" not in src
    assert "Failed to retrieve price alerts: {str(e)}" not in src
    assert "Market intervention failed: {str(e)}" not in src
    assert "Failed to generate dashboard summary: {str(e)}" not in src
