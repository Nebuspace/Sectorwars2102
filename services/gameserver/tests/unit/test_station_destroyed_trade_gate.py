"""WO-STATION-DESTROYED-TRADE-GATE: can_player_trade refuses destroyed stations."""

from types import SimpleNamespace

from src.services.trading_service import TradingService


def _player(**kwargs):
    defaults = dict(is_docked=True, current_sector_id=1)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _station(**kwargs):
    defaults = dict(sector_id=1, is_destroyed=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_can_player_trade_ok_when_functional():
    ok, reason = TradingService.can_player_trade(_player(), _station())
    assert ok is True
    assert reason == "OK"


def test_can_player_trade_refuses_destroyed_station():
    ok, reason = TradingService.can_player_trade(
        _player(), _station(is_destroyed=True)
    )
    assert ok is False
    assert "destroyed" in reason.lower()


def test_can_player_trade_still_requires_docked():
    ok, reason = TradingService.can_player_trade(
        _player(is_docked=False), _station(is_destroyed=False)
    )
    assert ok is False
    assert "docked" in reason.lower()


def test_can_player_trade_still_requires_same_sector():
    ok, reason = TradingService.can_player_trade(
        _player(current_sector_id=2), _station(sector_id=1, is_destroyed=False)
    )
    assert ok is False
    assert "sector" in reason.lower()
