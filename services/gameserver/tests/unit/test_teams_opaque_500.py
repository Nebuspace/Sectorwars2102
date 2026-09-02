"""LEG-3595 — teams.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3561 admin_messages / LEG-3569 claim_ship / LEG-3570 colonization opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import teams as teams_mod
from src.api.routes.teams import CreateTeamRequest, create_team, get_treasury_balance


@pytest.mark.asyncio
async def test_create_team_unexpected_is_opaque_500():
    """Outer create_team catch must not echo raw Exception text."""
    secret = "secret-create-team-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    request = CreateTeamRequest(name="Alpha Squadron")

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.create_team.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await create_team(request=request, player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_CREATE_FAILED",
        "detail": "Failed to create team",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_treasury_balance_unexpected_is_opaque_500():
    """get_treasury_balance catch must not echo raw Exception text."""
    secret = "secret-treasury-balance-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4(), team_id=team_id)

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.get_treasury_balance.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_treasury_balance(
                team_id=team_id,
                current_player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get treasury balance"
    assert secret not in str(exc.detail)


def test_teams_http500_catches_have_no_detail_str_e():
    """LEG-3595 — static pin: all fourteen HTTP 500 catch paths stay opaque."""
    src = Path(teams_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        "ERR_TEAMS_CREATE_FAILED",
        "ERR_TEAMS_UPDATE_FAILED",
        "ERR_TEAMS_DELETE_FAILED",
        "ERR_TEAMS_INVITE_FAILED",
        "ERR_TEAMS_JOIN_FAILED",
        "ERR_TEAMS_LEAVE_FAILED",
        "ERR_TEAMS_REMOVE_MEMBER_FAILED",
        'detail="Failed to update role"',
        'detail="Failed to transfer leadership"',
        'detail="Failed to deposit"',
        'detail="Failed to withdraw"',
        'detail="Failed to transfer"',
        'detail="Failed to get treasury balance"',
        'detail="Failed to get treasury history"',
    ):
        assert stable in src
    assert "Failed to create team: {str(e)}" not in src
    assert "Failed to update team: {str(e)}" not in src
    assert "Failed to delete team: {str(e)}" not in src
    assert "Failed to invite player: {str(e)}" not in src
    assert "Failed to join team: {str(e)}" not in src
    assert "Failed to leave team: {str(e)}" not in src
    assert "Failed to remove member: {str(e)}" not in src
    assert "Failed to update role: {str(e)}" not in src
    assert "Failed to transfer leadership: {str(e)}" not in src
    assert "Failed to deposit: {str(e)}" not in src
    assert "Failed to withdraw: {str(e)}" not in src
    assert "Failed to transfer: {str(e)}" not in src
    assert "Failed to get treasury balance: {str(e)}" not in src
    assert "Failed to get treasury history: {str(e)}" not in src
