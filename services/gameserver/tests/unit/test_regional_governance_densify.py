"""LEG-3932 — regional_governance policy-create returns structured 500."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import regional_governance as rg_mod


def test_regional_governance_policy_create_http500_is_structured():
    src = Path(rg_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_POLICY_CREATE_FAILED" in src
    assert "route_internal_error" in src
    assert 'HTTPException(status_code=500, detail="ERR_POLICY_CREATE_FAILED")' not in src
