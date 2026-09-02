"""LEG-3845/3848 — teams CRUD/invite/join/leave/remove unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import teams as teams_mod
from src.api.routes.teams import (
    CreateTeamRequest,
    InvitePlayerRequest,
    JoinTeamRequest,
    UpdateTeamRequest,
    create_team,
    delete_team,
    invite_player,
    join_team,
    leave_team,
    remove_member,
    update_team,
)


@pytest.mark.asyncio
async def test_create_team_unexpected_returns_structured_500():
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
async def test_update_team_unexpected_returns_structured_500():
    secret = "secret-update-team-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = UpdateTeamRequest(description="updated")

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.update_team.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await update_team(
                team_id=team_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_UPDATE_FAILED",
        "detail": "Failed to update team",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_delete_team_unexpected_returns_structured_500():
    secret = "secret-delete-team-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.delete_team.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await delete_team(team_id=team_id, player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_DELETE_FAILED",
        "detail": "Failed to delete team",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_invite_player_unexpected_returns_structured_500():
    secret = "secret-invite-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = InvitePlayerRequest(player_nickname="pilot1")

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.invite_player.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await invite_player(
                team_id=team_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_INVITE_FAILED",
        "detail": "Failed to invite player",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_join_team_unexpected_returns_structured_500():
    secret = "secret-join-team-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    request = JoinTeamRequest(team_id=uuid.uuid4())

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.join_team.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await join_team(request=request, player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_JOIN_FAILED",
        "detail": "Failed to join team",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_leave_team_unexpected_returns_structured_500():
    secret = "secret-leave-team-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.leave_team.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await leave_team(player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_LEAVE_FAILED",
        "detail": "Failed to leave team",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_remove_member_unexpected_returns_structured_500():
    secret = "secret-remove-member-should-not-leak"
    team_id = uuid.uuid4()
    member_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.remove_member.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await remove_member(
                team_id=team_id,
                member_id=member_id,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_REMOVE_MEMBER_FAILED",
        "detail": "Failed to remove member",
    }
    assert secret not in str(exc.detail)


def test_teams_densify_http500_catches_are_structured():
    """LEG-3845/3848 — static pin: densified team 500 catch paths emit error_code + detail."""
    src = Path(teams_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_TEAMS_CREATE_FAILED",
        "ERR_TEAMS_UPDATE_FAILED",
        "ERR_TEAMS_DELETE_FAILED",
        "ERR_TEAMS_INVITE_FAILED",
        "ERR_TEAMS_JOIN_FAILED",
        "ERR_TEAMS_LEAVE_FAILED",
        "ERR_TEAMS_REMOVE_MEMBER_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    for bare in (
        'detail="Failed to create team"',
        'detail="Failed to update team"',
        'detail="Failed to delete team"',
        'detail="Failed to invite player"',
        'detail="Failed to join team"',
        'detail="Failed to leave team"',
        'detail="Failed to remove member"',
    ):
        assert bare not in src
