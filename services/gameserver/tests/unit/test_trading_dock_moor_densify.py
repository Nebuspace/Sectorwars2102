"""LEG-3875 — trading dock/moor unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import trading as trading_mod
from src.api.routes.trading import (
    ERR_TRADING_DOCKING_FAILED,
    ERR_TRADING_MOORING_FAILED,
    ERR_TRADING_RELEASE_MOORING_FAILED,
    ERR_TRADING_UNDOCKING_FAILED,
    undock_from_port,
)


@pytest.mark.asyncio
async def test_undock_from_port_returns_structured_500():
    secret = "secret-undock-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4(), is_docked=True, turns=5, current_port_id=None, current_sector_id=1)
    player_query = MagicMock()
    player_query.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = player
    db = MagicMock()
    db.query.return_value = player_query
    with patch.object(trading_mod, "regenerate_turns"), patch.object(trading_mod.docking_service, "release"), patch.object(trading_mod, "spend_turns", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await undock_from_port(db=db, current_user=None, current_player=player)
    assert excinfo.value.detail == {"error_code": ERR_TRADING_UNDOCKING_FAILED, "detail": "Undocking failed"}
    assert secret not in str(excinfo.value.detail)


def test_trading_dock_moor_http500_catches_are_structured():
    src = Path(trading_mod.__file__).read_text(encoding="utf-8")
    for code in (ERR_TRADING_DOCKING_FAILED, ERR_TRADING_UNDOCKING_FAILED, ERR_TRADING_MOORING_FAILED, ERR_TRADING_RELEASE_MOORING_FAILED):
        assert code in src
    assert src.count("ERR_TRADING_DOCKING_FAILED") >= 1
    assert 'detail="Docking failed"' not in src
    assert 'detail="Undocking failed"' not in src
    assert 'detail="Long-term mooring failed"' not in src
    assert 'detail="Releasing long-term mooring failed"' not in src
    # Buy/sell Trade-failed densify (LEG-3911 / #1407) also structured now
    assert "ERR_TRADING_BUY_FAILED" in src
    assert "ERR_TRADING_SELL_FAILED" in src
    assert 'detail="Trade failed"' not in src
