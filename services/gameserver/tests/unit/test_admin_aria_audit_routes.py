"""LEG-3498 — admin.aria.audit scope on per-player ARIA security-ops routes."""

from __future__ import annotations

from pathlib import Path

_GS_ROOT = Path(__file__).resolve().parents[2]
_COMP_SRC = (_GS_ROOT / "src" / "api" / "routes" / "admin_comprehensive.py").read_text()


def _extract_route_block(source: str, route_marker: str) -> str:
    return source.split(route_marker, 1)[1].split("@router.", 1)[0]


class TestAriaAuditPlayerSecurityRoutes:
    def test_risk_assessment_requires_aria_audit(self):
        block = _extract_route_block(
            _COMP_SRC, '@router.get("/security/player/{player_id}/risk"'
        )
        assert "require_scope(ARIA_AUDIT)" in block
        assert "require_scope(PLAYERS_VIEW)" not in block
        assert block.index("require_scope(ARIA_AUDIT)") < block.index("get_security_service")

    def test_security_status_requires_aria_audit(self):
        block = _extract_route_block(
            _COMP_SRC, '@router.get("/security/player/{player_id}/status"'
        )
        assert "require_scope(ARIA_AUDIT)" in block
        assert "require_scope(PLAYERS_VIEW)" not in block
        assert block.index("require_scope(ARIA_AUDIT)") < block.index("get_security_service")

    def test_security_action_requires_aria_audit(self):
        block = _extract_route_block(
            _COMP_SRC, '@router.post("/security/player/{player_id}/action"'
        )
        assert "require_scope(ARIA_AUDIT)" in block
        assert "require_scope(SECURITY_ACT)" not in block
        assert block.index("require_scope(ARIA_AUDIT)") < block.index("get_db")

    def test_report_and_alerts_still_players_view(self):
        report = _extract_route_block(_COMP_SRC, '@router.get("/security/report"')
        alerts = _extract_route_block(_COMP_SRC, '@router.get("/security/alerts"')
        assert "require_scope(PLAYERS_VIEW)" in report
        assert "require_scope(ARIA_AUDIT)" not in report
        assert "require_scope(PLAYERS_VIEW)" in alerts
        assert "require_scope(ARIA_AUDIT)" not in alerts

    def test_cleanup_still_security_act(self):
        block = _extract_route_block(_COMP_SRC, '@router.post("/security/cleanup"')
        assert "require_scope(SECURITY_ACT)" in block
        assert "require_scope(ARIA_AUDIT)" not in block
