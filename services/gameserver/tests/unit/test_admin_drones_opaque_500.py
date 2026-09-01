"""LEG-3691 — admin_drones list + statistics HTTP 500 must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_drones as drones_mod
from src.api.routes.admin_drones import get_all_drones, get_drone_statistics


class _BoomAsyncDB:
    async def execute(self, *args, **kwargs):
        raise RuntimeError("secret-drones-query-should-not-leak")


@pytest.mark.asyncio
async def test_get_all_drones_unexpected_is_opaque_500():
    """LEG-3691 — drone list catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_all_drones(
            skip=0,
            limit=100,
            player_id=None,
            team_id=None,
            sector_id=None,
            drone_type=None,
            status=None,
            admin=SimpleNamespace(),
            db=_BoomAsyncDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to list drones"
    assert "secret-drones-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_drone_statistics_unexpected_is_opaque_500():
    """LEG-3691 — drone statistics catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_drone_statistics(
            admin=SimpleNamespace(),
            db=_BoomAsyncDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch drone statistics"
    assert "secret-drones-query-should-not-leak" not in str(exc.detail)


def test_admin_drones_http500_catches_have_no_detail_str_e():
    """LEG-3691 — static pin: list + statistics 500 details stay opaque."""
    src = Path(drones_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to list drones"',
        'detail="Failed to fetch drone statistics"',
    ):
        assert stable in src
    assert "Failed to list drones: {str(e)}" not in src
    assert "Failed to fetch drone statistics: {str(e)}" not in src
