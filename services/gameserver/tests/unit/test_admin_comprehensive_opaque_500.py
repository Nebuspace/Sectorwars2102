"""LEG-3582 — admin_comprehensive representative GET cluster must not echo Exception text.

Mirrors LEG-3570 admin_colonization / LEG-3569 claim_ship opaque densify.
Representative cluster: players/sectors/ports/planets/analytics/health/warp/teams.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from src.api.routes import admin_comprehensive as ac
from src.api.routes.admin_comprehensive import get_players_comprehensive


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-comp-query-should-not-leak")


def test_get_players_comprehensive_unexpected_is_opaque_500():
    """LEG-3582 — players catch must not echo raw Exception text."""
    try:
        _run(
            get_players_comprehensive(
                current_admin=SimpleNamespace(username="admin"),
                db=_BoomDB(),
            )
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 500
        assert exc.detail == "Failed to fetch players"
        assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


def test_admin_comprehensive_representative_cluster_http500_opaque():
    """LEG-3582 — static pin: representative GET cluster 500 details stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to fetch players"',
        'detail="Failed to fetch sectors"',
        'detail="Failed to fetch ports"',
        'detail="Failed to fetch planets"',
        'detail="Failed to fetch analytics"',
        'detail="Failed to get system health"',
        'detail="Failed to fetch warp tunnels"',
        'detail="Failed to fetch teams"',
    ):
        assert stable in src
    assert "Failed to fetch players: {str(e)}" not in src
    assert "Failed to fetch sectors: {str(e)}" not in src
    assert "Failed to fetch ports: {str(e)}" not in src
    assert "Failed to fetch planets: {str(e)}" not in src
    assert "Failed to get system health: {str(e)}" not in src
    assert "Failed to fetch warp tunnels: {str(e)}" not in src
    assert "Failed to fetch teams: {str(e)}" not in src
