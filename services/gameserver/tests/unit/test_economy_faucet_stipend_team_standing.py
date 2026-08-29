"""Daily guild stipend consumes team standing when teamed (LEG-1914).

Proves ``daily_stipend_amount`` uses ``resolve_effective_faction_standing_value``
→ level for eligibility when ``team_id`` is present, so team AVERAGE at
RECOGNIZED unlocks stipend that solo personal NEUTRAL would deny.
Magnitudes (PER_FACTION_DAILY_BY_LEVEL / GLOBAL_DAILY_STIPEND_CAP) unchanged.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.models.reputation import ReputationLevel
from src.services import economy_faucet_service as faucet


def _rep(level: ReputationLevel, numeric: int, *, faction_id=None):
    return types.SimpleNamespace(
        current_level=level,
        numeric_level=numeric,
        faction_id=faction_id or uuid4(),
    )


@pytest.mark.unit
class TestDailyStipendTeamStanding:
    def test_team_average_recognized_unlocks_when_personal_neutral(
        self, monkeypatch
    ):
        faction_id = uuid4()
        # Personal row is NEUTRAL (would deny); team AVERAGE maps to RECOGNIZED.
        personal = _rep(ReputationLevel.NEUTRAL, 0, faction_id=faction_id)
        player = types.SimpleNamespace(
            id=uuid4(),
            team_id=uuid4(),
            faction_reputations=[personal],
        )

        class _Q:
            def filter(self, *a, **k):
                return self

            def all(self):
                return [personal]

        session = MagicMock()
        session.query = lambda *_a, **_k: _Q()
        monkeypatch.setattr(faucet, "object_session", lambda _p: session)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (50, "team"),  # RECOGNIZED floor
        )

        class _FakeSvc:
            def __init__(self, _db):
                pass

            def _calculate_reputation_level(self, value):
                assert value == 50
                return ReputationLevel.RECOGNIZED

        monkeypatch.setattr(
            "src.services.faction_service.FactionService", _FakeSvc
        )

        amount = faucet.daily_stipend_amount(player)
        assert amount == faucet.PER_FACTION_DAILY_BY_LEVEL[
            ReputationLevel.RECOGNIZED.value
        ]

    def test_solo_personal_path_unchanged_when_no_team(self, monkeypatch):
        """Detached / no team_id keeps existing personal Reputation path."""
        monkeypatch.setattr(faucet, "object_session", lambda _p: None)
        player = types.SimpleNamespace(
            id=uuid4(),
            team_id=None,
            faction_reputations=[
                _rep(ReputationLevel.RECOGNIZED, 1),
                _rep(ReputationLevel.NEUTRAL, 0),
            ],
        )
        assert faucet.daily_stipend_amount(player) == 5

    def test_team_below_floor_still_denies(self, monkeypatch):
        faction_id = uuid4()
        personal = _rep(ReputationLevel.NEUTRAL, 0, faction_id=faction_id)
        player = types.SimpleNamespace(
            id=uuid4(),
            team_id=uuid4(),
            faction_reputations=[personal],
        )

        class _Q:
            def filter(self, *a, **k):
                return self

            def all(self):
                return [personal]

        session = MagicMock()
        session.query = lambda *_a, **_k: _Q()
        monkeypatch.setattr(faucet, "object_session", lambda _p: session)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "team"),  # NEUTRAL
        )

        class _FakeSvc:
            def __init__(self, _db):
                pass

            def _calculate_reputation_level(self, value):
                return ReputationLevel.NEUTRAL

        monkeypatch.setattr(
            "src.services.faction_service.FactionService", _FakeSvc
        )
        assert faucet.daily_stipend_amount(player) == 0
