"""LEG-3844 — admin_drones unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes import admin_drones as ad_mod
from src.api.routes.admin_drones import get_all_drones, get_drone_statistics


@pytest.mark.asyncio
async def test_get_all_drones_unexpected_returns_structured_500():
    secret = "secret-drone-list-should-not-leak"
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_all_drones(skip=0, limit=100, admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_DRONES_LIST_FAILED",
        "detail": "Failed to list drones",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_drone_statistics_unexpected_returns_structured_500():
    secret = "secret-drone-stats-should-not-leak"
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_drone_statistics(admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_DRONES_STATISTICS_FAILED",
        "detail": "Failed to fetch drone statistics",
    }
    assert secret not in str(exc.detail)


def test_admin_drones_http500_catches_are_structured():
    """LEG-3844 — static pin: drone admin 500 catch paths emit error_code + detail."""
    src = Path(ad_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_DRONES_LIST_FAILED",
        "ERR_ADMIN_DRONES_STATISTICS_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to list drones"' not in src
    assert 'detail="Failed to fetch drone statistics"' not in src
