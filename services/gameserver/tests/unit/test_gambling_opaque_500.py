"""LEG-3954 — gambling.py spin/roll/buy-ticket routes must not echo Exception text on 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import gambling as gambling_mod
from src.api.routes.gambling import (
    DiceRollRequest,
    LotteryTicketRequest,
    SlotSpinRequest,
    buy_lottery_ticket,
    roll_dice,
    spin_slots,
)
from src.models.player import Player
from src.models.station import Station


def _docked_player(*, credits: int = 10_000):
    return SimpleNamespace(
        id=uuid4(),
        credits=credits,
        is_docked=True,
        current_port_id=uuid4(),
        settings={},
    )


def _gambling_db(player, *, commit_raises: str | None = None):
    """Mock Session with Player lock + SpaceDock station lookup."""
    locked = SimpleNamespace(
        id=player.id,
        credits=player.credits,
        is_docked=player.is_docked,
        current_port_id=player.current_port_id,
        settings=dict(player.settings or {}),
    )
    station = SimpleNamespace(is_spacedock=True)

    def query_side_effect(model):
        q = MagicMock()
        if model is Player:
            q.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = locked
        elif model is Station:
            q.filter.return_value.first.return_value = station
        return q

    db = MagicMock()
    db.query.side_effect = query_side_effect
    if commit_raises:
        db.commit = MagicMock(side_effect=RuntimeError(commit_raises))
    else:
        db.commit = MagicMock()
    db.rollback = MagicMock()
    return db


@pytest.mark.asyncio
async def test_spin_slots_unexpected_is_opaque_500():
    secret = "secret-spin-slots-should-not-leak"
    player = _docked_player()
    db = _gambling_db(player, commit_raises=secret)

    with pytest.raises(HTTPException) as excinfo:
        await spin_slots(
            request=SlotSpinRequest(bet_amount=100),
            db=db,
            current_user=SimpleNamespace(id=uuid4()),
            current_player=player,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_GAMBLING_SLOTS_SPIN_FAILED",
        "detail": "Failed to spin slots",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_roll_dice_unexpected_is_opaque_500():
    secret = "secret-roll-dice-should-not-leak"
    player = _docked_player()
    db = _gambling_db(player, commit_raises=secret)

    with pytest.raises(HTTPException) as excinfo:
        await roll_dice(
            request=DiceRollRequest(bet_amount=100, bet_type="high"),
            db=db,
            current_user=SimpleNamespace(id=uuid4()),
            current_player=player,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_GAMBLING_DICE_ROLL_FAILED",
        "detail": "Failed to roll dice",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_buy_lottery_ticket_unexpected_is_opaque_500():
    secret = "secret-lottery-buy-should-not-leak"
    player = _docked_player(credits=10_000)
    db = _gambling_db(player, commit_raises=secret)

    with pytest.raises(HTTPException) as excinfo:
        await buy_lottery_ticket(
            request=LotteryTicketRequest(numbers=[1, 2, 3, 4], bet_amount=500),
            db=db,
            current_user=SimpleNamespace(id=uuid4()),
            current_player=player,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_GAMBLING_LOTTERY_BUY_FAILED",
        "detail": "Failed to buy lottery ticket",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_gambling_http500_catches_have_no_detail_str_e():
    """LEG-3954 — static pin: spin/roll/buy-ticket HTTP 500 catch paths stay opaque."""
    src = Path(gambling_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    for code in (
        "ERR_GAMBLING_SLOTS_SPIN_FAILED",
        "ERR_GAMBLING_DICE_ROLL_FAILED",
        "ERR_GAMBLING_LOTTERY_BUY_FAILED",
    ):
        assert code in src
    assert "Failed to spin slots: {str(e)}" not in src
    assert "Failed to roll dice: {str(e)}" not in src
    assert "Failed to buy lottery ticket: {str(e)}" not in src
    assert src.count("route_internal_error(") >= 3
