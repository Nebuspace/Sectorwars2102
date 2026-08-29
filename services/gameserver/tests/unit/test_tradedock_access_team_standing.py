"""TradeDock access consumes team aggregate standing (LEG-2819).

Proves tradedock_access uses resolve_effective_faction_standing_value so a
teamed player can reach full/guest access via team standing even when personal
rep is below threshold; solo path unchanged.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import ReputationLevel
from src.models.team import Team
from src.services import construction_service


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


def _station(*, faction_name: str = "Terran Federation"):
    return SimpleNamespace(
        id=uuid4(),
        faction_affiliation=faction_name,
    )


def _faction(*, faction_id=None, name="Terran Federation"):
    faction = Faction()
    faction.id = faction_id or uuid4()
    faction.name = name
    faction.faction_type = FactionType.FEDERATION
    return faction


class TestTradedockAccessTeamStanding:
    def test_solo_player_guest_access_unchanged(self, monkeypatch):
        player_id = uuid4()
        faction = _faction()
        player = Player(id=player_id, team_id=None)

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (100, "personal"),
        )

        db = _FakeDb(faction=faction, player=player)
        access_level, rep_value = construction_service.tradedock_access(
            db, player, _station(faction_name=faction.name)
        )
        assert rep_value == 100
        assert access_level == "guest"

    def test_teamed_player_full_access_via_team_aggregate(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Dock Team", leader_id=player_id)
        faction = _faction(faction_id=faction_id)

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
        access_level, rep_value = construction_service.tradedock_access(
            db, player, _station(faction_name=faction.name)
        )
        assert rep_value == 250
        assert access_level == "full"

    def test_teamed_player_denied_when_team_standing_non_positive(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Low Team", leader_id=player_id)
        faction = _faction(faction_id=faction_id)

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 0,
                        "level": ReputationLevel.NEUTRAL.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(faction=faction, player=player, team=team)
        access_level, rep_value = construction_service.tradedock_access(
            db, player, _station(faction_name=faction.name)
        )
        assert rep_value == 0
        assert access_level == "denied"
