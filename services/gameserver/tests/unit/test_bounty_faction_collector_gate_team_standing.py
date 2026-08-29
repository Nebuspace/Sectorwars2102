"""Faction bounty collector gate consumes team aggregate standing (LEG-1888).

Proves _collector_passes_faction_gate uses resolve_effective_faction_standing_value
so a teamed player can collect via team standing even when personal rep is below
RECOGNIZED; solo path unchanged.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.faction import FactionType
from src.models.player import Player
from src.models.reputation import ReputationLevel
from src.models.team import Team
from src.services import bounty_service


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


class TestFactionBountyCollectorGateTeamStanding:
    def test_solo_player_below_gate_fails(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        collector = Player(id=player_id, team_id=None)
        faction = SimpleNamespace(id=faction_id, faction_type=FactionType.FEDERATION)

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (10, "personal"),
        )

        db = _FakeDb(faction=faction, player=collector)
        assert bounty_service._collector_passes_faction_gate(
            db, collector, FactionType.FEDERATION
        ) is False

    def test_solo_player_at_gate_passes(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        collector = Player(id=player_id, team_id=None)
        faction = SimpleNamespace(id=faction_id, faction_type=FactionType.FEDERATION)

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (50, "personal"),
        )

        db = _FakeDb(faction=faction, player=collector)
        assert bounty_service._collector_passes_faction_gate(
            db, collector, FactionType.FEDERATION
        ) is True

    def test_teamed_player_passes_via_team_standing(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        collector = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Bounty Team", leader_id=player_id)
        faction = SimpleNamespace(id=faction_id, faction_type=FactionType.FEDERATION)

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 200,
                        "level": ReputationLevel.TRUSTED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(faction=faction, player=collector, team=team)
        assert bounty_service._collector_passes_faction_gate(
            db, collector, FactionType.FEDERATION
        ) is True

    def test_teamed_player_denied_when_team_standing_below_gate(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        collector = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Low Team", leader_id=player_id)
        faction = SimpleNamespace(id=faction_id, faction_type=FactionType.FEDERATION)

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 10,
                        "level": ReputationLevel.NEUTRAL.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(faction=faction, player=collector, team=team)
        assert bounty_service._collector_passes_faction_gate(
            db, collector, FactionType.FEDERATION
        ) is False

    def test_missing_faction_fails_closed(self, monkeypatch):
        collector = Player(id=uuid4(), team_id=None)

        def _boom(*_a, **_k):
            raise AssertionError("resolver should not run when faction row is missing")

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _boom,
        )

        db = _FakeDb(faction=None)
        assert bounty_service._collector_passes_faction_gate(
            db, collector, FactionType.FEDERATION
        ) is False
