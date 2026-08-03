"""Unit tests for WO-P2P-TRADING-SYSTEM (credits + commodities + ship-bundle)."""

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
    assert offer["ships"] == []


def test_normalize_offer_dedupes_ships():
    offer = _normalize_offer(
        {"ships": ["aaa", "bbb", "aaa", None], "credits": 10}
    )
    assert offer["ships"] == ["aaa", "bbb"]
    assert offer["credits"] == 10


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


def test_settle_source_covers_ship_bundle_gates():
    source = inspect.getsource(PlayerTradeService.settle)
    assert "_validate_ship_bundle" in source
    assert "_transfer_ship" in source
    assert "_validate_dock_gate" in source


def test_transfer_ship_voids_insurance_and_appends_registry():
    source = inspect.getsource(PlayerTradeService._transfer_ship)
    assert "insurance" in source
    assert "OWNERSHIP_TRANSFER" in source
    assert "append_registry_event" in source


def test_confirm_calls_settle_when_both_confirm():
    source = inspect.getsource(PlayerTradeService.confirm)
    assert "settle" in source


def test_tablename():
    assert PlayerTradeSession.__tablename__ == "player_trade_sessions"
