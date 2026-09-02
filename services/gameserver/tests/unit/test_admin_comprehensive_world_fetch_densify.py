"""LEG-3934 — admin_comprehensive structured 500 densify (world)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3934_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_SECTORS_LIST_FAILED",
        "ERR_ADMIN_COMP_PORTS_LIST_FAILED",
        "ERR_ADMIN_COMP_PLANETS_LIST_FAILED",
        "ERR_ADMIN_COMP_WARP_TUNNELS_LIST_FAILED",
        "ERR_ADMIN_COMP_TEAMS_LIST_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
