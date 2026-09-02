"""LEG-3935 — admin_comprehensive structured 500 densify (analytics)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3935_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_ANALYTICS_FETCH_FAILED",
        "ERR_ADMIN_COMP_SYSTEM_HEALTH_FAILED",
        "ERR_ADMIN_COMP_ANALYTICS_DASHBOARD_FAILED",
        "ERR_ADMIN_COMP_ANALYTICS_PREDICTIONS_FAILED",
        "ERR_ADMIN_COMP_ANALYTICS_SNAPSHOT_FAILED",
        "ERR_ADMIN_COMP_PORT_STOCK_UPDATE_FAILED",
        "ERR_ADMIN_COMP_AI_BEHAVIOR_ANALYTICS_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
