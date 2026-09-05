"""LEG-3378 — emergent Shadow Syndicate +10 on surviving full Wanted timer cycle."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from unittest.mock import patch

import pytest

from src.models.faction import FactionType
from src.models.player import Player
from src.services import wanted_service
from src.services.emergent_reputation_service import EMERGENT_ACTIONS
from src.services.wanted_service import (
    SURVIVE_WANTED_CYCLE_SS_SETTINGS_KEY,
    WANTED_DURATION,
)

FROZEN_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def test_survive_wanted_cycle_ss_registered() -> None:
    action = EMERGENT_ACTIONS["SURVIVE_WANTED_CYCLE_SS"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.SYNDICATE, 10)
    ]
    assert "Wanted Status cycle" in action.doc_source or "cycle" in action.doc_source.lower()


def _player(
    *,
    is_wanted: bool = True,
    wanted_declared_at: Optional[datetime] = None,
    wanted_until: Optional[datetime] = None,
    settings: Optional[dict] = None,
) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_wanted=is_wanted,
        wanted_declared_at=wanted_declared_at,
        wanted_until=wanted_until,
        settings=settings or {},
    )


class _SweepFakeQuery:
    def __init__(self, rows: list):
        self._rows = rows

    def filter(self, *a: Any, **k: Any) -> "_SweepFakeQuery":
        return self

    def all(self) -> list:
        return self._rows


class _SweepFakeSession:
    def __init__(self, players: list):
        self._players = players
        self.flush_calls = 0

    def query(self, target: Any) -> _SweepFakeQuery:
        assert target is Player
        return _SweepFakeQuery(self._players)

    def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.unit
class TestSurviveWantedCycleSsEmergentRep:
    def test_full_timer_cycle_dispatches_emergent_action_once(self) -> None:
        declared = FROZEN_NOW - WANTED_DURATION
        until = declared + WANTED_DURATION  # exact bust-timer expiry
        player = _player(
            wanted_declared_at=declared,
            wanted_until=until,
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            mock_apply.return_value = {"success": True, "action": "SURVIVE_WANTED_CYCLE_SS"}
            count = wanted_service.clear_expired_wanted(db, now=FROZEN_NOW)

        assert count == 1
        mock_apply.assert_called_once()
        args, _kwargs = mock_apply.call_args
        assert args[1] is player
        assert args[2] == "SURVIVE_WANTED_CYCLE_SS"
        assert player.is_wanted is False
        ledger = player.settings.get(SURVIVE_WANTED_CYCLE_SS_SETTINGS_KEY)
        assert declared.isoformat() in ledger

    def test_sub_full_cycle_does_not_dispatch(self) -> None:
        declared = FROZEN_NOW - timedelta(hours=12)
        player = _player(
            wanted_declared_at=declared,
            wanted_until=FROZEN_NOW - timedelta(seconds=1),
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            wanted_service.clear_expired_wanted(db, now=FROZEN_NOW)

        mock_apply.assert_not_called()
        assert player.is_wanted is False

    def test_same_cycle_anchor_does_not_double_award(self) -> None:
        declared = FROZEN_NOW - WANTED_DURATION - timedelta(hours=1)
        until = FROZEN_NOW - timedelta(seconds=1)
        cycle_key = declared.isoformat()
        player = _player(
            wanted_declared_at=declared,
            wanted_until=until,
            settings={SURVIVE_WANTED_CYCLE_SS_SETTINGS_KEY: [cycle_key]},
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            wanted_service.clear_expired_wanted(db, now=FROZEN_NOW)

        mock_apply.assert_not_called()

    def test_rep_failure_never_blocks_clear_sweep(self) -> None:
        declared = FROZEN_NOW - WANTED_DURATION
        until = declared + WANTED_DURATION
        player = _player(
            wanted_declared_at=declared,
            wanted_until=until,
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action",
            side_effect=RuntimeError("simulated rep outage"),
        ):
            count = wanted_service.clear_expired_wanted(db, now=FROZEN_NOW)

        assert count == 1
        assert player.is_wanted is False
        assert player.wanted_until is None
        assert player.wanted_declared_at is None
        assert db.flush_calls == 1
