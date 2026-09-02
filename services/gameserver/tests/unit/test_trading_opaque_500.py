"""LEG-3604 — trading.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3595 teams.py / LEG-3581 audit.py opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import trading as trading_mod
from src.api.routes.trading import TradeRequest, buy_resource, undock_from_port
from tests.unit.test_trading_core_pins import (
    _market_price,
    _neutral_player,
    _neutral_station,
    _session_for,
    _ship,
)


@pytest.mark.asyncio
async def test_buy_resource_unexpected_is_opaque_500():
    """Outer buy_resource catch must not echo raw Exception text."""
    secret = "secret-buy-trade-should-not-leak"
    player = _neutral_player(credits=10_000)
    station = _neutral_station()
    ship = _ship(capacity=100)
    mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
    db = _session_for(player, station, ship, mp, player_seq_len=1)

    def _commit_raises() -> None:
        db.commit_calls += 1
        raise RuntimeError(secret)

    db.commit = _commit_raises  # type: ignore[method-assign]

    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._record_aria_trade_hooks", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as excinfo:
            await buy_resource(
                trade_request=TradeRequest(
                    station_id=str(station.id),
                    resource_type="ore",
                    quantity=10,
                ),
                db=db,
                current_user=None,
                current_player=player,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Trade failed"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_undock_from_port_unexpected_is_opaque_500():
    """Outer undock_from_port catch must not echo raw Exception text."""
    secret = "secret-undock-should-not-leak"
    player = SimpleNamespace(
        id=uuid.uuid4(),
        is_docked=True,
        turns=5,
        current_port_id=None,
        current_sector_id=1,
    )

    player_query = MagicMock()
    player_query.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = player
    db = MagicMock()
    db.query.return_value = player_query

    with patch.object(trading_mod, "regenerate_turns"), \
         patch.object(trading_mod.docking_service, "release"), \
         patch.object(trading_mod, "spend_turns", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await undock_from_port(
                db=db,
                current_user=None,
                current_player=player,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_TRADING_UNDOCKING_FAILED",
        "detail": "Undocking failed",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_trading_http500_catches_have_no_detail_str_e():
    """LEG-3604 / LEG-3875 — Trade stays opaque string; dock/moor are structured."""
    src = Path(trading_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Trade failed"' in src
    for code in (
        "ERR_TRADING_DOCKING_FAILED",
        "ERR_TRADING_UNDOCKING_FAILED",
        "ERR_TRADING_MOORING_FAILED",
        "ERR_TRADING_RELEASE_MOORING_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Docking failed"' not in src
    assert 'detail="Undocking failed"' not in src
    assert 'detail="Long-term mooring failed"' not in src
    assert 'detail="Releasing long-term mooring failed"' not in src
    assert "Trade failed: {str(e)}" not in src
    assert "Docking failed: {str(e)}" not in src
    assert "Undocking failed: {str(e)}" not in src
    assert "Long-term mooring failed: {str(e)}" not in src
    assert "Releasing long-term mooring failed: {str(e)}" not in src
