"""LEG-3937 — admin_comprehensive structured 500 densify (world)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3937_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_WARP_TUNNELS_LIST_FAILED",
        "ERR_ADMIN_COMP_SECTOR_UPDATE_FAILED",
        "ERR_ADMIN_COMP_PLANET_CREATE_FAILED",
        "ERR_ADMIN_COMP_PLANET_UPDATE_FAILED",
        "ERR_ADMIN_COMP_PLANET_DELETE_FAILED",
        "ERR_ADMIN_COMP_PORT_CREATE_FAILED",
        "ERR_ADMIN_COMP_SECTOR_WARPS_FAILED",
        "ERR_ADMIN_COMP_WARP_CREATE_FAILED",
        "ERR_ADMIN_COMP_WARP_UPDATE_FAILED",
        "ERR_ADMIN_COMP_WARP_DELETE_FAILED",
        "ERR_ADMIN_COMP_PORT_DELETE_FAILED",
        "ERR_ADMIN_COMP_PORT_IN_SECTOR_CREATE_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
