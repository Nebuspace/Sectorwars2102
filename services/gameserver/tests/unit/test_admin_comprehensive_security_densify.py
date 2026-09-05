"""LEG-3936 — admin_comprehensive structured 500 densify (security)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3936_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_SECURITY_REPORT_FAILED",
        "ERR_ADMIN_COMP_SECURITY_ALERTS_FAILED",
        "ERR_ADMIN_COMP_PLAYER_RISK_FAILED",
        "ERR_ADMIN_COMP_PLAYER_SECURITY_STATUS_FAILED",
        "ERR_ADMIN_COMP_PLAYER_SECURITY_LOGS_FAILED",
        "ERR_ADMIN_COMP_SECURITY_CLEANUP_FAILED",
        "ERR_ADMIN_COMP_SECURITY_ACTION_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
