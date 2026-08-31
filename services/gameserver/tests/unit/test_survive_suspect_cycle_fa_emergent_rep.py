"""LEG-3377 — emergent Fringe Alliance +15 on surviving full Suspect Status cycle."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from unittest.mock import patch

import pytest

from src.models.faction import FactionType
from src.models.player import Player
from src.services import suspect_service
from src.services.emergent_reputation_service import EMERGENT_ACTIONS
from src.services.suspect_service import SURVIVE_SUSPECT_CYCLE_FA_SETTINGS_KEY

FROZEN_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def test_survive_suspect_cycle_fa_registered() -> None:
    action = EMERGENT_ACTIONS["SURVIVE_SUSPECT_CYCLE_FA"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.OUTLAWS, 15)
    ]
    assert "1+ hour" in action.doc_source or "cycle" in action.doc_source.lower()


def _player(
    *,
    is_suspect: bool = True,
    suspect_declared_at: Optional[datetime] = None,
    suspect_until: Optional[datetime] = None,
    suspect_team_snapshot: Optional[List[uuid.UUID]] = None,
    settings: Optional[dict] = None,
) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_suspect=is_suspect,
        suspect_declared_at=suspect_declared_at,
        suspect_until=suspect_until,
        suspect_team_snapshot=suspect_team_snapshot,
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
class TestSurviveSuspectCycleFaEmergentRep:
    def test_full_hour_cycle_dispatches_emergent_action_once(self) -> None:
        declared = FROZEN_NOW - timedelta(hours=1)
        until = declared + timedelta(hours=1)
        player = _player(
            suspect_declared_at=declared,
            suspect_until=until,
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            mock_apply.return_value = {"success": True, "action": "SURVIVE_SUSPECT_CYCLE_FA"}
            count = suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        assert count == 1
        mock_apply.assert_called_once()
        args, _kwargs = mock_apply.call_args
        assert args[1] is player
        assert args[2] == "SURVIVE_SUSPECT_CYCLE_FA"
        assert player.is_suspect is False
        ledger = player.settings.get(SURVIVE_SUSPECT_CYCLE_FA_SETTINGS_KEY)
        assert declared.isoformat() in ledger

    def test_sub_hour_cycle_does_not_dispatch(self) -> None:
        declared = FROZEN_NOW - timedelta(minutes=30)
        player = _player(
            suspect_declared_at=declared,
            suspect_until=FROZEN_NOW - timedelta(seconds=1),
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        mock_apply.assert_not_called()
        assert player.is_suspect is False

    def test_same_cycle_anchor_does_not_double_award(self) -> None:
        declared = FROZEN_NOW - timedelta(hours=2)
        until = declared + timedelta(hours=2)
        cycle_key = declared.isoformat()
        player = _player(
            suspect_declared_at=declared,
            suspect_until=until,
            settings={SURVIVE_SUSPECT_CYCLE_FA_SETTINGS_KEY: [cycle_key]},
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        mock_apply.assert_not_called()

    def test_rep_failure_never_blocks_clear_sweep(self) -> None:
        declared = FROZEN_NOW - timedelta(hours=1)
        until = declared + timedelta(hours=1)
        player = _player(
            suspect_declared_at=declared,
            suspect_until=until,
            suspect_team_snapshot=[uuid.uuid4()],
        )
        db = _SweepFakeSession([player])

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action",
            side_effect=RuntimeError("simulated rep outage"),
        ):
            count = suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        assert count == 1
        assert player.is_suspect is False
        assert player.suspect_until is None
        assert player.suspect_declared_at is None
        assert player.suspect_team_snapshot is None
        assert db.flush_calls == 1
