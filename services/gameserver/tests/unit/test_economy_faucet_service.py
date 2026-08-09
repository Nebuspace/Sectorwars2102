"""Unit coverage for economy_faucet_service stipend helpers + weekly early-exit.

Deepens beyond lock-key AST checks in test_scheduler_lock_keys.py.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from src.models.reputation import ReputationLevel
from src.services import economy_faucet_service as faucet


def _rep(level: ReputationLevel, numeric: int):
    return types.SimpleNamespace(
        current_level=level,
        numeric_level=numeric,
    )


def _player(*, credits=0, settings=None, reps=None):
    return types.SimpleNamespace(
        id="p1",
        credits=credits,
        settings=dict(settings or {}),
        faction_reputations=list(reps or []),
    )


@pytest.fixture(autouse=True)
def _detached_player_session(monkeypatch):
    """SimpleNamespace players are unmapped — force relationship fallback."""
    monkeypatch.setattr(faucet, "object_session", lambda _obj: None)


def test_daily_stipend_amount_sums_good_standing_only():
    player = _player(
        reps=[
            _rep(ReputationLevel.RECOGNIZED, 1),  # 5
            _rep(ReputationLevel.NEUTRAL, 0),  # ignored
            _rep(ReputationLevel.EXALTED, 8),  # 50
        ],
    )
    assert faucet.daily_stipend_amount(player) == 55


def test_daily_stipend_amount_clamps_to_global_cap():
    # 3× EXALTED = 150 → capped at GLOBAL_DAILY_STIPEND_CAP (100)
    player = _player(
        reps=[_rep(ReputationLevel.EXALTED, 8) for _ in range(3)],
    )
    assert faucet.daily_stipend_amount(player) == faucet.GLOBAL_DAILY_STIPEND_CAP


def test_daily_stipend_amount_zero_when_none_qualify():
    player = _player(
        reps=[
            _rep(ReputationLevel.NEUTRAL, 0),
            _rep(ReputationLevel.SUSPICIOUS, -1),
        ],
    )
    assert faucet.daily_stipend_amount(player) == 0


def test_apply_daily_already_paid_today_is_idempotent(monkeypatch):
    player = _player(
        credits=10,
        settings={faucet._DAILY_STIPEND_ANCHOR_KEY: "2026-08-09"},
        reps=[_rep(ReputationLevel.EXALTED, 8)],
    )
    monkeypatch.setattr(faucet, "flag_modified", lambda *a, **k: None)

    granted = faucet.apply_daily_rep_stipend_for_player(player, "2026-08-09")

    assert granted == 0
    assert player.credits == 10


def test_apply_daily_credits_and_anchors(monkeypatch):
    player = _player(
        credits=100,
        reps=[_rep(ReputationLevel.RECOGNIZED, 1)],
    )
    monkeypatch.setattr(faucet, "flag_modified", lambda *a, **k: None)

    granted = faucet.apply_daily_rep_stipend_for_player(player, "2026-08-09")

    assert granted == 5
    assert player.credits == 105
    assert player.settings[faucet._DAILY_STIPEND_ANCHOR_KEY] == "2026-08-09"


def test_apply_daily_zero_amount_still_anchors(monkeypatch):
    player = _player(credits=7, reps=[])
    monkeypatch.setattr(faucet, "flag_modified", lambda *a, **k: None)

    granted = faucet.apply_daily_rep_stipend_for_player(player, "2026-08-09")

    assert granted == 0
    assert player.credits == 7
    assert player.settings[faucet._DAILY_STIPEND_ANCHOR_KEY] == "2026-08-09"


def test_run_weekly_faucet_sync_skips_when_lock_not_held(monkeypatch):
    db = MagicMock()
    db.execute.return_value.scalar.return_value = False
    monkeypatch.setattr(
        "src.core.database.SessionLocal", lambda: db, raising=False,
    )
    # Patch where the function imports SessionLocal
    import src.core.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    result = faucet.run_weekly_faucet_sync()

    assert result == {"citizen_grants": 0, "total_credits": 0, "week": -1}
    db.close.assert_called()
