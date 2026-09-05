"""LEG-3847 — admin_fleets list unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_fleets as af_mod
from src.api.routes.admin_fleets import get_all_fleets


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-fleets-list-should-not-leak")


@pytest.mark.asyncio
async def test_get_all_fleets_unexpected_returns_structured_500():
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
    assert "secret-fleets-list-should-not-leak" not in str(exc.detail)


def test_admin_fleets_http500_catches_are_structured():
    """LEG-3847 — static pin: fleet list 500 catch path emits error_code + detail."""
    src = Path(af_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_ADMIN_FLEETS_LIST_FAILED" in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch fleets"' not in src
