"""Dock reputation gate consumes team aggregate standing (LEG-814).

Proves check_reputation_gate uses resolve_effective_faction_standing_value so a
teamed player can pass a station threshold via team standing even when personal
rep is below threshold; solo path unchanged.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.player import Player
from src.models.reputation import ReputationLevel
from src.models.team import Team
from src.services import docking_service


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeDb:
    def __init__(self, *, faction=None, player=None, team=None):
        self._faction = faction
        self._player = player
        self._team = team

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "Faction":
            return _FakeQuery(self._faction)
        if name == "Player":
            return _FakeQuery(self._player)
        if name == "Team":
            return _FakeQuery(self._team)
        return _FakeQuery(None)


def _station(*, threshold: int = 50, faction_name: str = "Terran Federation"):
    return SimpleNamespace(
        id=uuid4(),
        faction_affiliation=faction_name,
        reputation_threshold=threshold,
    )


class TestDockingReputationGateTeamStanding:
    def test_solo_player_uses_personal_rep_unchanged(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        player = Player(id=player_id, team_id=None)
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")

        def _resolve(db, pid, fid, *, team_id=None):
            assert pid == player_id
            assert fid == faction_id
            return 10, "personal"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )

        db = _FakeDb(faction=faction, player=player)
        allowed, rep_value, threshold = docking_service.check_reputation_gate(
            db, _station(threshold=50), player
        )
        assert threshold == 50
        assert rep_value == 10
        assert allowed is False

    def test_solo_player_passes_when_personal_rep_meets_threshold(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        player = Player(id=player_id, team_id=None)
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (100, "personal"),
        )

        db = _FakeDb(faction=faction, player=player)
        allowed, rep_value, _threshold = docking_service.check_reputation_gate(
            db, _station(threshold=50), player
        )
        assert rep_value == 100
        assert allowed is True

    def test_teamed_player_uses_team_aggregate_for_dock_gate(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Dock Team", leader_id=player_id)
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 250,
                        "level": ReputationLevel.TRUSTED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(faction=faction, player=player, team=team)
        allowed, rep_value, threshold = docking_service.check_reputation_gate(
            db, _station(threshold=200), player
        )
        assert threshold == 200
        assert rep_value == 250
        assert allowed is True

    def test_teamed_player_denied_when_team_standing_below_threshold(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Low Team", leader_id=player_id)
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 50,
                        "level": ReputationLevel.RECOGNIZED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(faction=faction, player=player, team=team)
        allowed, rep_value, threshold = docking_service.check_reputation_gate(
            db, _station(threshold=200), player
        )
        assert threshold == 200
        assert rep_value == 50
        assert allowed is False

    def test_zero_threshold_skips_gate_without_resolver(self, monkeypatch):
        player = Player(id=uuid4(), team_id=uuid4())
        called = {"n": 0}

        def _boom(*_a, **_k):
            called["n"] += 1
            raise AssertionError("resolver should not run when threshold is 0")

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _boom,
        )

        db = _FakeDb()
        allowed, rep_value, threshold = docking_service.check_reputation_gate(
            db, _station(threshold=0), player
        )
        assert allowed is True
        assert rep_value == 0
        assert threshold == 0
        assert called["n"] == 0
