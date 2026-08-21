"""Live trade price multiplier consumes team aggregate standing (LEG-1915).

Proves ``compute_player_price_multiplier`` routes the faction layer through
``resolve_effective_faction_standing_value`` / ``trade_modifier_from_standing_value``
so team AVERAGE changes the live unit-price multiplier when solo personal
standing would not.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import trading_service
from src.services.faction_service import trade_modifier_from_standing_value


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeDb:
    def __init__(self, *, faction=None):
        self._faction = faction

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "Faction":
            return _FakeQuery(self._faction)
        return _FakeQuery(None)


@pytest.mark.unit
class TestComputePlayerPriceMultiplierTeamStanding:
    def test_teamed_player_uses_team_aggregate_faction_layer(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        player = SimpleNamespace(
            id=player_id,
            team_id=uuid4(),
            reputation_tier="Neutral",
            settings={},
        )
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=faction_id, name="Federation")

        # Personal would be NEUTRAL (0 → 1.0); team AVERAGE FRIENDLY (100 → 0.97).
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (100, "team"),
        )

        mult = trading_service.compute_player_price_multiplier(
            _FakeDb(faction=faction), player, station
        )
        assert mult == pytest.approx(trade_modifier_from_standing_value(100))
        assert mult == pytest.approx(0.97)

    def test_solo_personal_path_unchanged_shape(self, monkeypatch):
        player = SimpleNamespace(
            id=uuid4(),
            team_id=None,
            reputation_tier="Neutral",
            settings={},
        )
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "personal"),
        )
        mult = trading_service.compute_player_price_multiplier(
            _FakeDb(faction=faction), player, station
        )
        assert mult == pytest.approx(1.0)

    def test_personal_rep_and_first_login_layers_still_compose(self, monkeypatch):
        player = SimpleNamespace(
            id=uuid4(),
            team_id=uuid4(),
            reputation_tier="Legendary",  # 0.90 personal layer
            settings={"trade_bonus": 0.1},  # first-login ×0.9
        )
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (100, "team"),  # 0.97 faction
        )
        mult = trading_service.compute_player_price_multiplier(
            _FakeDb(faction=faction), player, station
        )
        assert mult == pytest.approx(0.97 * 0.90 * 0.9)

    def test_unaffiliated_station_skips_faction_layer(self, monkeypatch):
        called = {"n": 0}

        def _resolve(*_a, **_k):
            called["n"] += 1
            return 100, "team"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )
        player = SimpleNamespace(
            id=uuid4(), team_id=uuid4(), reputation_tier="Neutral", settings={}
        )
        station = SimpleNamespace(faction_affiliation=None)
        mult = trading_service.compute_player_price_multiplier(
            _FakeDb(faction=None), player, station
        )
        assert called["n"] == 0
        assert mult == pytest.approx(1.0)
