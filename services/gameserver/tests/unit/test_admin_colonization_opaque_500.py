"""LEG-3570 — admin_colonization HTTP 500 catches must not echo Exception text.

Mirrors LEG-3561 admin_messages / LEG-3569 claim_ship opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from src.api.routes import admin_colonization as ac
from src.api.routes.admin_colonization import get_colony_production


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-colonization-query-should-not-leak")


def test_get_colony_production_unexpected_is_opaque_500():
    """LEG-3570 — production catch must not echo raw Exception text."""
    try:
        _run(get_colony_production(timeRange="day", current_admin=SimpleNamespace(), db=_BoomDB()))
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "Failed to fetch production data"
        assert "secret-colonization-query-should-not-leak" not in str(exc.detail)


def test_admin_colonization_http500_catches_have_no_detail_str_e():
    """LEG-3570 — static pin: the four HTTP 500 catch paths stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to fetch production data"',
        'detail="Failed to fetch genesis device data"',
        'detail="Failed to fetch planetary data"',
        'detail="Failed to tick planet production"',
    ):
        assert stable in src
    assert "Failed to fetch production data: {str(e)}" not in src
    assert "Failed to fetch genesis device data: {str(e)}" not in src
    assert "Failed to fetch planetary data: {str(e)}" not in src
    assert "Failed to tick planet production: {str(e)}" not in src
