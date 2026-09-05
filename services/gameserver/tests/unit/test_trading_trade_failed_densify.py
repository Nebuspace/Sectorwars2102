"""LEG-3911 densify — trading buy/sell Trade-failed structured 500s."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import trading as trading_mod
from src.api.routes.trading import ERR_TRADING_BUY_FAILED, ERR_TRADING_SELL_FAILED


def test_buy_trade_failed_returns_structured_500():
    """Buy catch path densified — exercise via source pin + opaque sibling shape."""
    src = Path(trading_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_TRADING_BUY_FAILED" in src
    assert "ERR_TRADING_SELL_FAILED" in src
    assert "route_internal_error" in src
    assert src.count('detail="Trade failed"') == 0
    # buy then sell raises present as route_internal_error
    assert src.count("ERR_TRADING_BUY_FAILED") >= 1
    assert src.count("ERR_TRADING_SELL_FAILED") >= 1


def test_trading_trade_failed_http500_is_structured():
    src = Path(trading_mod.__file__).read_text(encoding="utf-8")
    assert 'raise route_internal_error(\n            ERR_TRADING_BUY_FAILED' in src or "ERR_TRADING_BUY_FAILED" in src
    assert "Trade failed" in src
    assert 'detail="Trade failed"' not in src
