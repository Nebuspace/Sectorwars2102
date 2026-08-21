"""Soft-ORDER invent=0 team standing — follow-ups 3–5 + bounty collector (#1961–#1964).

Wires consume ``resolve_effective_faction_standing_value``; thresholds/magnitudes
unchanged. Solo personal path still uses the resolver (personal source).
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import ReputationLevel
from src.models.team import Team
from src.services import bounty_service, construction_service
from src.services.faction_service import FactionService


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


# ---------------------------------------------------------------------------
# #1961 TradeDock construction gate
# ---------------------------------------------------------------------------


class TestTradedockPlayerRepTeamStanding:
    def test_solo_uses_personal_via_resolver(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")
        station = SimpleNamespace(faction_affiliation="Terran Federation")

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (50, "personal"),
        )

        db = _FakeDb(faction=faction)
        assert construction_service._tradedock_player_rep(db, player_id, station) == 50

    def test_team_aggregate_opens_full_gate_when_personal_would_guest(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        faction = SimpleNamespace(id=faction_id, name="Terran Federation")
        station = SimpleNamespace(faction_affiliation="Terran Federation")
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Dock Builders", leader_id=player_id)

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
        # Thresholds unchanged: >=200 full access
        assert construction_service._tradedock_player_rep(db, player_id, station) == 250

    def test_unaffiliated_station_returns_zero(self):
        station = SimpleNamespace(faction_affiliation=None)
        assert construction_service._tradedock_player_rep(_FakeDb(), uuid4(), station) == 0


# ---------------------------------------------------------------------------
# #1962 pricing modifier
# ---------------------------------------------------------------------------


class TestFactionPricingModifierTeamStanding:
    @pytest.mark.asyncio
    async def test_teamed_uses_team_value_through_faction_ladder(self, monkeypatch):
        faction_id = uuid4()
        player_id = uuid4()
        faction = Faction()
        faction.id = faction_id
        faction.name = "Terran Federation"
        faction.faction_type = FactionType.FEDERATION
        faction.base_pricing_modifier = 1.0

        async def _get_faction(_self, fid):
            assert fid == faction_id
            return faction

        monkeypatch.setattr(FactionService, "get_faction_by_id", _get_faction)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (650, "team"),
        )

        svc = FactionService(_FakeDb())
        result = await svc.get_faction_pricing_modifier(player_id, faction_id)
        assert result == faction.get_pricing_modifier(650)
        assert result == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# #1963 territory access
# ---------------------------------------------------------------------------


class TestTerritoryAccessTeamStanding:
    @pytest.mark.asyncio
    async def test_teamed_denied_when_team_standing_hostile(self, monkeypatch):
        sector_id = uuid4()
        faction_id = uuid4()
        faction = Faction()
        faction.id = faction_id
        faction.name = "Pirates United"
        faction.faction_type = FactionType.PIRATES
        faction.territory_sectors = [sector_id]

        async def _all(_self):
            return [faction]

        monkeypatch.setattr(FactionService, "get_all_factions", _all)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (-250, "team"),
        )

        svc = FactionService(_FakeDb())
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is False
        assert "Insufficient reputation" in result["reason"]

    @pytest.mark.asyncio
    async def test_teamed_allowed_when_team_standing_clears_gate(self, monkeypatch):
        sector_id = uuid4()
        faction_id = uuid4()
        faction = Faction()
        faction.id = faction_id
        faction.name = "Pirates United"
        faction.faction_type = FactionType.PIRATES
        faction.territory_sectors = [sector_id]

        async def _all(_self):
            return [faction]

        monkeypatch.setattr(FactionService, "get_all_factions", _all)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (-100, "team"),
        )

        svc = FactionService(_FakeDb())
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# #1964 bounty collector gate
# ---------------------------------------------------------------------------


class TestBountyCollectorFactionGateTeamStanding:
    def test_solo_recognized_passes(self, monkeypatch):
        collector = Player(id=uuid4(), team_id=None)
        faction = SimpleNamespace(
            id=uuid4(), faction_type=FactionType.FEDERATION, name="Terran Federation"
        )

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (100, "personal"),  # RECOGNIZED band
        )

        db = _FakeDb(faction=faction)
        assert (
            bounty_service._collector_passes_faction_gate(
                db, collector, FactionType.FEDERATION
            )
            is True
        )

    def test_team_average_opens_when_solo_personal_would_fail(self, monkeypatch):
        collector = Player(id=uuid4(), team_id=uuid4())
        faction = SimpleNamespace(
            id=uuid4(), faction_type=FactionType.FEDERATION, name="Terran Federation"
        )

        # Team AVERAGE → RECOGNIZED (value 50+); personal would be 0 / NEUTRAL
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (80, "team"),
        )

        db = _FakeDb(faction=faction)
        assert (
            bounty_service._collector_passes_faction_gate(
                db, collector, FactionType.FEDERATION
            )
            is True
        )

    def test_missing_standing_fails_closed(self, monkeypatch):
        collector = Player(id=uuid4(), team_id=None)
        faction = SimpleNamespace(
            id=uuid4(), faction_type=FactionType.FEDERATION, name="Terran Federation"
        )

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "personal"),
        )

        db = _FakeDb(faction=faction)
        assert (
            bounty_service._collector_passes_faction_gate(
                db, collector, FactionType.FEDERATION
            )
            is False
        )

    def test_missing_faction_fails_closed(self):
        collector = Player(id=uuid4(), team_id=None)
        db = _FakeDb(faction=None)
        assert (
            bounty_service._collector_passes_faction_gate(
                db, collector, FactionType.FEDERATION
            )
            is False
        )
