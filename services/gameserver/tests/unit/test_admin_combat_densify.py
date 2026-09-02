"""LEG-3854 — admin_combat unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_combat as mod
from src.api.routes.admin_combat import (
    ERR_ADMIN_COMBAT_BALANCE_FAILED,
    ERR_ADMIN_COMBAT_FEED_FAILED,
    get_combat_balance_analytics,
    get_live_combat_feed,
)


@pytest.mark.asyncio
async def test_get_live_combat_feed_boom_returns_structured_500():
    secret = "secret-combat-feed-should-not-leak"

    with patch.object(mod, "CombatAnalyticsService") as svc_cls:
        svc_cls.return_value.get_live_combat_feed.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_live_combat_feed(
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_COMBAT_FEED_FAILED,
        "detail": "Failed to retrieve combat feed",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_combat_balance_analytics_boom_returns_structured_500():
    secret = "secret-balance-analytics-should-not-leak"

    with patch.object(mod, "CombatAnalyticsService") as svc_cls:
        svc_cls.return_value.get_combat_balance_analytics.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_combat_balance_analytics(
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_COMBAT_BALANCE_FAILED,
        "detail": "Failed to retrieve balance analytics",
    }
    assert secret not in str(exc.detail)


def test_admin_combat_densify_is_structured():
    """LEG-3854 — static pin: combat admin 500 catch paths emit error_code + detail."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for err in (
        ERR_ADMIN_COMBAT_FEED_FAILED,
        ERR_ADMIN_COMBAT_BALANCE_FAILED,
        "ERR_ADMIN_COMBAT_INTERVENE_FAILED",
        "ERR_ADMIN_COMBAT_DISPUTES_FAILED",
        "ERR_ADMIN_COMBAT_DASHBOARD_FAILED",
    ):
        assert err in src
        assert f"route_internal_error({err}" in src
    assert 'detail="Failed to retrieve combat feed"' not in src
    assert 'detail="Failed to retrieve balance analytics"' not in src
    assert ") from e" not in src
