"""Construction queue faction_rep_tier consumes team standing (LEG-1912).

Proves ``_faction_rep_tier`` / ``_sorted_queue`` route through
``resolve_effective_faction_standing_value`` so team AVERAGE reorders the
queue relative to solo personal standing.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import construction_service


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


def _reservation(*, player_id, bumps=0, deposit=100, created_at=None):
    return SimpleNamespace(
        player_id=player_id,
        state="queued",
        priority_bumps_count=bumps,
        deposit_paid=deposit,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.unit
class TestFactionRepTierTeamStanding:
    def test_teamed_average_raises_tier_vs_solo_neutral(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=faction_id, name="Federation")

        # Solo personal NEUTRAL (0 → ordinal 0); team AVERAGE RECOGNIZED (50 → 1).
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (50, "team"),
        )
        tier = construction_service._faction_rep_tier(
            _FakeDb(faction=faction), player_id, station
        )
        assert tier == 1  # RECOGNIZED

    def test_solo_neutral_value_is_zero_tier(self, monkeypatch):
        player_id = uuid4()
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "personal"),
        )
        tier = construction_service._faction_rep_tier(
            _FakeDb(faction=faction), player_id, station
        )
        assert tier == 0

    def test_sorted_queue_prefers_higher_team_tier(self, monkeypatch):
        high_player = uuid4()
        low_player = uuid4()
        station = SimpleNamespace(faction_affiliation="Federation")
        faction = SimpleNamespace(id=uuid4(), name="Federation")

        def _resolve(db, pid, fid, *, team_id=None):
            if pid == high_player:
                return (50, "team")  # RECOGNIZED → 1
            return (0, "personal")  # NEUTRAL → 0

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 1, 2, tzinfo=timezone.utc)
        # Same bumps/deposit; earlier created_at would win without tier —
        # high team tier must reorder ahead of low personal.
        low = _reservation(player_id=low_player, created_at=early)
        high = _reservation(player_id=high_player, created_at=late)
        ordered = construction_service._sorted_queue(
            _FakeDb(faction=faction), station, [low, high]
        )
        assert [r.player_id for r in ordered] == [high_player, low_player]

    def test_unaffiliated_station_tier_zero(self):
        station = SimpleNamespace(faction_affiliation=None)
        assert (
            construction_service._faction_rep_tier(
                _FakeDb(faction=None), uuid4(), station
            )
            == 0
        )
