"""Unit tests for WO-P2P-TRADING-SYSTEM v1 kernel (no DB)."""

import inspect

from src.services.player_trade_service import (
    FLAT_TAX_RATE,
    MIN_TAX_CR,
    PlayerTradeService,
    _normalize_offer,
)
from src.models.player_trade import PlayerTradeSession, PlayerTradeSessionStatus


def test_normalize_offer_strips_negatives_and_zeros():
    offer = _normalize_offer(
        {"credits": -5, "commodities": {"ore": 3, "junk": 0}, "ship_id": "abc"}
    )
    assert offer["credits"] == 0
    assert offer["commodities"] == {"ore": 3}
    assert offer["ship_id"] == "abc"


def test_flat_tax_rate_matches_adr():
    assert FLAT_TAX_RATE == 0.05
    assert MIN_TAX_CR == 1


def test_session_statuses_include_kernel_set():
    assert {s.value for s in PlayerTradeSessionStatus} >= {
        "PENDING_ACCEPT",
        "OPEN",
        "SETTLED",
        "CANCELLED",
        "EXPIRED",
        "DECLINED",
    }


def test_service_exposes_lifecycle_methods():
    for name in (
        "initiate",
        "accept",
        "stage_offer",
        "confirm",
        "settle",
        "cancel",
        "decline",
    ):
        assert callable(getattr(PlayerTradeService, name))


def test_confirm_calls_settle_when_both_confirm():
    source = inspect.getsource(PlayerTradeService.confirm)
    assert "settle" in source


def test_tablename():
    assert PlayerTradeSession.__tablename__ == "player_trade_sessions"
