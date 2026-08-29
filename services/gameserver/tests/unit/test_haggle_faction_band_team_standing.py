"""Haggle faction-band factor consumes team standing (LEG-1913).

Proves ``_faction_band_factor`` routes through
``resolve_effective_faction_standing_value`` so team AVERAGE widens/narrows
the band vs solo personal standing. Lerp endpoints unchanged.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import haggle_service


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
class TestHaggleFactionBandTeamStanding:
    def test_team_average_allied_narrows_band_vs_solo_neutral(self, monkeypatch):
        player = SimpleNamespace(id=uuid4(), team_id=uuid4())
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")

        solo_neutral = haggle_service._lerp_by_value(
            0, haggle_service.FACTION_BAND_HOSTILE, haggle_service.FACTION_BAND_ALLIED
        )
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (1000, "team"),  # allied endpoint
        )
        team_factor = haggle_service._faction_band_factor(
            _FakeDb(faction=faction), player, station
        )
        assert team_factor == pytest.approx(haggle_service.FACTION_BAND_ALLIED)
        assert team_factor < solo_neutral  # allied = easier/narrower vs neutral mid

    def test_team_hostile_widens_band_vs_solo_neutral(self, monkeypatch):
        player = SimpleNamespace(id=uuid4(), team_id=uuid4())
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")
        solo_neutral = haggle_service._lerp_by_value(
            0, haggle_service.FACTION_BAND_HOSTILE, haggle_service.FACTION_BAND_ALLIED
        )
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (-1000, "team"),
        )
        team_factor = haggle_service._faction_band_factor(
            _FakeDb(faction=faction), player, station
        )
        assert team_factor == pytest.approx(haggle_service.FACTION_BAND_HOSTILE)
        assert team_factor > solo_neutral

    def test_unaffiliated_station_returns_neutral(self, monkeypatch):
        called = {"n": 0}

        def _resolve(*_a, **_k):
            called["n"] += 1
            return 1000, "team"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )
        player = SimpleNamespace(id=uuid4(), team_id=uuid4())
        station = SimpleNamespace(faction_affiliation=None)
        assert (
            haggle_service._faction_band_factor(_FakeDb(faction=None), player, station)
            == 1.0
        )
        assert called["n"] == 0
