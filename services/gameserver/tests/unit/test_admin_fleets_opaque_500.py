"""LEG-3690 — admin_fleets get_all_fleets HTTP 500 must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_fleets as af_mod
from src.api.routes.admin_fleets import get_all_fleets


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-fleet-query-should-not-leak")


@pytest.mark.asyncio
async def test_get_all_fleets_unexpected_is_opaque_500():
    """LEG-3690 — fleet list catch must not echo raw Exception text."""
    secret = "secret-fleet-query-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_all_fleets(
            status=None,
            team_id=None,
            sector_id=None,
            in_battle=None,
            skip=0,
            limit=100,
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_FLEETS_LIST_FAILED",
        "detail": "Failed to fetch fleets",
    }
    assert secret not in str(exc.detail)


def test_admin_fleets_get_all_fleets_http500_is_opaque():
    """LEG-3690 — static pin: get_all_fleets 500 detail stays opaque."""
    src = Path(af_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_ADMIN_FLEETS_LIST_FAILED" in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch fleets"' not in src
    assert 'detail=f"Failed to fetch fleets: {e}"' not in src
    assert "Failed to fetch fleets: {str(e)}" not in src
