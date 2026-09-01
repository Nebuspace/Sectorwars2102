"""LEG-3629 — admin_combat.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3628 admin.py opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_combat as ac_mod
from src.api.routes.admin_combat import (
    get_combat_balance_analytics,
    get_live_combat_feed,
)


@pytest.mark.asyncio
async def test_get_live_combat_feed_unexpected_is_opaque_500():
    """Combat feed catch must not echo raw Exception text."""
    secret = "secret-combat-feed-should-not-leak"

    with patch.object(ac_mod, "CombatAnalyticsService") as svc_cls:
        svc_cls.return_value.get_live_combat_feed.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_live_combat_feed(
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to retrieve combat feed"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_combat_balance_analytics_unexpected_is_opaque_500():
    """Balance analytics catch must not echo raw Exception text."""
    secret = "secret-balance-analytics-should-not-leak"

    with patch.object(ac_mod, "CombatAnalyticsService") as svc_cls:
        svc_cls.return_value.get_combat_balance_analytics.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_combat_balance_analytics(
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to retrieve balance analytics"
    assert secret not in str(exc.detail)


def test_admin_combat_http500_catches_have_no_detail_str_e():
    """LEG-3629 — static pin: admin_combat 500 details stay opaque."""
    src = Path(ac_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to retrieve combat feed"',
        'detail="Failed to retrieve balance analytics"',
        'detail="Failed to retrieve combat disputes"',
        'detail="Failed to generate dashboard summary"',
    ):
        assert stable in src
    assert "Failed to retrieve combat feed: {str(e)}" not in src
    assert "Failed to retrieve balance analytics: {str(e)}" not in src
    assert "Failed to retrieve combat disputes: {str(e)}" not in src
    assert "Failed to generate dashboard summary: {str(e)}" not in src
