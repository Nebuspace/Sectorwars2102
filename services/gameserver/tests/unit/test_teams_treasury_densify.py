"""LEG-3855 — teams treasury unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import teams as teams_mod
from src.api.routes.teams import (
    DepositRequest,
    TransferRequest,
    WithdrawRequest,
    deposit_to_treasury,
    get_treasury_balance,
    get_treasury_history,
    transfer_to_player,
    withdraw_from_treasury,
)


@pytest.mark.asyncio
async def test_deposit_to_treasury_unexpected_returns_structured_500():
    secret = "secret-deposit-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = DepositRequest(resource_type="credits", amount=100)

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.deposit_to_treasury.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await deposit_to_treasury(
                team_id=team_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TREASURY_DEPOSIT_FAILED",
        "detail": "Failed to deposit",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_withdraw_from_treasury_unexpected_returns_structured_500():
    secret = "secret-withdraw-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = WithdrawRequest(resource_type="credits", amount=50)

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.withdraw_from_treasury.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await withdraw_from_treasury(
                team_id=team_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TREASURY_WITHDRAW_FAILED",
        "detail": "Failed to withdraw",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_transfer_to_player_unexpected_returns_structured_500():
    secret = "secret-transfer-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4())
    request = TransferRequest(
        recipient_nickname="pilot1",
        resource_type="credits",
        amount=25,
    )

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.transfer_to_player.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await transfer_to_player(
                team_id=team_id,
                request=request,
                player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TREASURY_TRANSFER_FAILED",
        "detail": "Failed to transfer",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_treasury_balance_unexpected_returns_structured_500():
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
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TREASURY_BALANCE_FAILED",
        "detail": "Failed to get treasury balance",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_treasury_history_unexpected_returns_structured_500():
    secret = "secret-treasury-history-should-not-leak"
    team_id = uuid.uuid4()
    player = SimpleNamespace(id=uuid.uuid4(), team_id=team_id)

    with patch.object(teams_mod, "TeamService") as svc_cls:
        svc_cls.return_value.get_treasury_history.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_treasury_history(
                team_id=team_id,
                skip=0,
                limit=25,
                current_player=player,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TEAMS_TREASURY_HISTORY_FAILED",
        "detail": "Failed to get treasury history",
    }
    assert secret not in str(exc.detail)


def test_teams_treasury_densify_http500_catches_are_structured():
    """LEG-3855 — static pin: treasury 500 catch paths emit error_code + detail."""
    src = Path(teams_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_TEAMS_TREASURY_DEPOSIT_FAILED",
        "ERR_TEAMS_TREASURY_WITHDRAW_FAILED",
        "ERR_TEAMS_TREASURY_TRANSFER_FAILED",
        "ERR_TEAMS_TREASURY_BALANCE_FAILED",
        "ERR_TEAMS_TREASURY_HISTORY_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    for bare in (
        'detail="Failed to deposit"',
        'detail="Failed to withdraw"',
        'detail="Failed to transfer"',
        'detail="Failed to get treasury balance"',
        'detail="Failed to get treasury history"',
    ):
        assert bare not in src
