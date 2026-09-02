"""LEG-3838 — claim_ship unexpected failures return structured 500s."""

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
async def test_claim_ship_unexpected_returns_structured_500():
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
    assert exc.detail == {
        "error_code": "ERR_FIRST_LOGIN_CLAIM_SHIP_FAILED",
        "detail": "Failed to claim ship",
    }
    assert secret not in str(exc.detail)


def test_claim_ship_outer_catch_is_structured():
    """LEG-3838 — static pin: outer claim_ship catch no longer uses Internal server error."""
    src = Path(fl.__file__).read_text(encoding="utf-8")
    assert "ERR_FIRST_LOGIN_CLAIM_SHIP_FAILED" in src
    assert 'detail="Internal server error"' not in src
    assert "Internal server error: {str(e)}" not in src
