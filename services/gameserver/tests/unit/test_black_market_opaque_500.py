"""LEG-3816 — black_market.py HTTP 500 catches must not echo Exception text.

Handlers already use opaque detail; this densifies buy/sell unexpected paths.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import black_market as black_market_mod
from src.api.routes.black_market import (
    BlackMarketTradeRequest,
    buy_contraband,
    sell_contraband,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


def _ship():
    return SimpleNamespace(id=uuid.uuid4())


def _station():
    return SimpleNamespace(id=uuid.uuid4())


def _trade_request():
    return BlackMarketTradeRequest(
        station_id=str(uuid.uuid4()),
        commodity="narcotics",
        quantity=1,
    )


@pytest.mark.asyncio
async def test_buy_contraband_unexpected_is_opaque_500():
    secret = "secret-black-market-buy-should-not-leak"
    db = MagicMock()

    with patch.object(black_market_mod, "_active_ship_or_404", return_value=_ship()), \
         patch.object(black_market_mod, "_get_station_or_404", return_value=_station()), \
         patch.object(
             black_market_mod.ContrabandService,
             "buy",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await buy_contraband(trade_request=_trade_request(), player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Black-market trade failed"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_sell_contraband_unexpected_is_opaque_500():
    secret = "secret-black-market-sell-should-not-leak"
    db = MagicMock()

    with patch.object(black_market_mod, "_active_ship_or_404", return_value=_ship()), \
         patch.object(black_market_mod, "_get_station_or_404", return_value=_station()), \
         patch.object(
             black_market_mod.ContrabandService,
             "sell",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await sell_contraband(trade_request=_trade_request(), player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Black-market trade failed"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_black_market_http500_catches_have_no_detail_str_e():
    """LEG-3816 — static pin: buy/sell 500 paths stay opaque."""
    src = Path(black_market_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Black-market trade failed"' in src
    assert 'detail=f"Black-market trade failed: {str(e)}"' not in src
    assert 'detail=f"Black-market trade failed: {e}"' not in src
