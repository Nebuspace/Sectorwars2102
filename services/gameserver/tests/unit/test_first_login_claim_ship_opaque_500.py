"""LEG-3569 — claim_ship HTTP 500 catches must not echo Exception text.

Mirrors LEG-3561 admin_messages / LEG-3551 place_gold_bubble opaque densify:
stable detail strings, secret RuntimeError substrings never appear in detail.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import first_login as fl
from src.api.routes.first_login import ShipClaimRequest, claim_ship


@pytest.mark.asyncio
async def test_claim_ship_unexpected_is_opaque_500():
    """Outer claim_ship catch must not echo raw Exception text."""
    secret = "secret-claim-ship-outer-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    claim = ShipClaimRequest(ship_type="SCOUT_SHIP", dialogue_response="mine")

    with patch.object(fl, "FirstLoginService") as svc_cls:
        svc_cls.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await claim_ship(
                claim=claim,
                player=player,
                db=MagicMock(),
                ai_service=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Internal server error"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_claim_ship_record_failure_is_opaque_500():
    """record_player_ship_claim failure must not echo raw Exception text."""
    secret = "secret-record-ship-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    claim = ShipClaimRequest(ship_type="SCOUT_SHIP", dialogue_response="mine")
    state = SimpleNamespace(current_session_id=uuid.uuid4())

    svc = MagicMock()
    svc.get_player_first_login_state.return_value = state
    svc.record_player_ship_claim.side_effect = RuntimeError(secret)

    with patch.object(fl, "FirstLoginService", return_value=svc):
        with pytest.raises(HTTPException) as excinfo:
            await claim_ship(
                claim=claim,
                player=player,
                db=MagicMock(),
                ai_service=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to record ship claim"
    assert secret not in str(exc.detail)


def test_claim_ship_http500_catches_have_no_detail_str_e():
    """Static pin: claim_ship 500 details stay opaque (no str(e) interpolation)."""
    src = Path(fl.__file__).read_text(encoding="utf-8")
    assert 'detail="Internal server error"' in src
    assert 'detail="Failed to record ship claim"' in src
    assert "Internal server error: {str(e)}" not in src
    assert "Failed to record ship claim: {str(record_error)}" not in src
