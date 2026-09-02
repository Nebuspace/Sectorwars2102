"""LEG-3938 — admin_comprehensive structured 500 densify (ai)."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import admin_comprehensive as ac


def test_admin_comprehensive_leg_3938_http500_catches_are_structured():
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_COMP_AI_MODELS_FAILED",
        "ERR_ADMIN_COMP_AI_PREDICTION_ACCURACY_FAILED",
        "ERR_ADMIN_COMP_AI_PLAYER_PROFILES_FAILED",
        "ERR_ADMIN_COMP_AI_SYSTEM_METRICS_FAILED",
        "ERR_ADMIN_COMP_AI_PREDICTIONS_FAILED",
        "ERR_ADMIN_COMP_AI_ROUTE_OPTIMIZATION_FAILED",
        "ERR_ADMIN_COMP_AI_BEHAVIOR_ANALYTICS_FAILED",
    ):
        assert code in src
    assert "from src.utils.error_handling import route_internal_error" in src
