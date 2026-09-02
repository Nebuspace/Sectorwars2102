"""LEG-3856 — teams role/leadership unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import teams as teams_mod
from src.api.routes.teams import (
    UpdateRoleRequest,
    transfer_leadership,
    update_member_role,
)


@pytest.mark.asyncio
async def test_update_member_role_unexpected_returns_structured_500():
    secret = "secret-update-role-should-not-leak"
    team_id = uuid.uuid4()
    member_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = UpdateRoleRequest(new_role="officer")

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.update_member_role.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await update_member_role(
                team_id=team_id,
                member_id=member_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_UPDATE_ROLE_FAILED",
        "detail": "Failed to update role",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_transfer_leadership_unexpected_returns_structured_500():
    secret = "secret-transfer-leadership-should-not-leak"
    team_id = uuid.uuid4()
    new_leader_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.transfer_leadership.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await transfer_leadership(
                team_id=team_id,
                new_leader_id=new_leader_id,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TRANSFER_LEADERSHIP_FAILED",
        "detail": "Failed to transfer leadership",
    }
    assert secret not in str(exc.detail)


def test_teams_role_densify_http500_catches_are_structured():
    """LEG-3856 — static pin: role/leadership 500 catch paths emit error_code + detail."""
    src = Path(teams_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_TEAMS_UPDATE_ROLE_FAILED",
        "ERR_TEAMS_TRANSFER_LEADERSHIP_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to update role"' not in src
    assert 'detail="Failed to transfer leadership"' not in src
