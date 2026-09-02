"""LEG-3853 — admin_colonization unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_colonization as mod
from src.api.routes.admin_colonization import (
    ERR_ADMIN_COLONIZATION_GENESIS_FAILED,
    ERR_ADMIN_COLONIZATION_PRODUCTION_FAILED,
    get_colony_production,
    get_genesis_devices,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-colonization-query-should-not-leak")


@pytest.mark.asyncio
async def test_get_colony_production_boom_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_colony_production(
            timeRange="day",
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_COLONIZATION_PRODUCTION_FAILED,
        "detail": "Failed to fetch production data",
    }
    assert "secret-colonization-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_genesis_devices_boom_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_genesis_devices(
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_ADMIN_COLONIZATION_GENESIS_FAILED,
        "detail": "Failed to fetch genesis device data",
    }
    assert "secret-colonization-query-should-not-leak" not in str(exc.detail)


def test_admin_colonization_densify_is_structured():
    """LEG-3853 — static pin: colonization admin 500 catch paths emit error_code + detail."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for err in (
        ERR_ADMIN_COLONIZATION_PRODUCTION_FAILED,
        ERR_ADMIN_COLONIZATION_GENESIS_FAILED,
        "ERR_ADMIN_COLONIZATION_PLANETS_FAILED",
        "ERR_ADMIN_COLONIZATION_TICK_FAILED",
    ):
        assert err in src
        assert f"route_internal_error({err}" in src
    assert 'detail="Failed to fetch production data"' not in src
    assert 'detail="Failed to fetch genesis device data"' not in src
