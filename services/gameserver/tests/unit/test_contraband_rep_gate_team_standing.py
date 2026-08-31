"""Black-market Fringe access gate consumes team aggregate standing (LEG-1895).

Proves ``ContrabandService._passes_rep_gate`` uses
``resolve_effective_faction_standing_value`` so a teamed player can open the
contraband catalog via team AVERAGE when personal Fringe standing is below
RECOGNIZED; solo personal path unchanged. Detection ``rep_term`` is out of
scope (ADR-0062 personal axis).
"""
from types import SimpleNamespace
from uuid import uuid4

from src.models.faction import FactionType
from src.models.player import Player
from src.models.reputation import ReputationLevel
from src.models.team import Team
from src.services.contraband_service import (
    GATE_FACTION,
    GATE_MIN_LEVEL,
    ContrabandService,
)


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


def _outlaws_faction(*, faction_id=None):
    return SimpleNamespace(
        id=faction_id or uuid4(),
        faction_type=FactionType.OUTLAWS,
        name="Fringe Alliance",
    )


class TestContrabandRepGateTeamStanding:
    def test_gate_constants_unchanged(self):
        assert GATE_FACTION == FactionType.OUTLAWS
        assert GATE_MIN_LEVEL == ReputationLevel.RECOGNIZED

    def test_missing_outlaws_faction_fails_closed(self):
        svc = ContrabandService(_FakeDb(faction=None))
        assert svc._passes_rep_gate(uuid4()) is False

    def test_solo_personal_below_recognized_fails(self, monkeypatch):
        player_id = uuid4()
        faction = _outlaws_faction()
        player = Player(id=player_id, team_id=None)

        def _resolve(db, pid, fid, *, team_id=None):
            assert pid == player_id
            assert fid == faction.id
            # NEUTRAL band (< +50) — below RECOGNIZED
            return 0, "personal"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )

        svc = ContrabandService(_FakeDb(faction=faction, player=player))
        assert svc._passes_rep_gate(player_id) is False

    def test_solo_personal_recognized_passes(self, monkeypatch):
        player_id = uuid4()
        faction = _outlaws_faction()
        player = Player(id=player_id, team_id=None)

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (50, "personal"),
        )

        svc = ContrabandService(_FakeDb(faction=faction, player=player))
        assert svc._passes_rep_gate(player_id) is True

    def test_team_average_opens_gate_when_personal_would_fail(self, monkeypatch):
        """Accept: team AVERAGE ≥ RECOGNIZED opens catalog; solo personal would 404."""
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        faction = _outlaws_faction(faction_id=faction_id)
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Fringe Crew", leader_id=player_id)

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        # AVERAGE ≥ +50 → RECOGNIZED; personal would be below
                        "value": 75,
                        "level": ReputationLevel.RECOGNIZED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        svc = ContrabandService(
            _FakeDb(faction=faction, player=player, team=team)
        )
        assert svc._passes_rep_gate(player_id) is True

    def test_team_below_recognized_still_fails(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        faction = _outlaws_faction(faction_id=faction_id)
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Green Crew", leader_id=player_id)

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

        svc = ContrabandService(
            _FakeDb(faction=faction, player=player, team=team)
        )
        assert svc._passes_rep_gate(player_id) is False
