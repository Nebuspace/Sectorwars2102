"""LEG-3933 — admin_comprehensive structured 500 densify (players)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3933_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_PLAYERS_LIST_FAILED",
        "ERR_ADMIN_COMP_PLAYER_UPDATE_FAILED",
        "ERR_ADMIN_COMP_SHIP_CREATE_FAILED",
        "ERR_ADMIN_COMP_SHIP_UPDATE_FAILED",
        "ERR_ADMIN_COMP_SHIP_DELETE_FAILED",
        "ERR_ADMIN_COMP_SHIP_TELEPORT_FAILED",
        "ERR_ADMIN_COMP_PLAYER_CREATE_FAILED",
        "ERR_ADMIN_COMP_PLAYERS_BULK_CREATE_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
