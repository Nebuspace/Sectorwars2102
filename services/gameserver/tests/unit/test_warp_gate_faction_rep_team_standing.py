"""Warp-gate faction_rep layers consume team aggregate standing (LEG-1906).

Proves `_faction_rep_value` / `_check_faction_rep_layers` route through
``resolve_effective_faction_standing_value`` so a teamed player can clear a
rep_min layer via team AVERAGE when personal standing alone would fail.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.faction import FactionType
from src.models.player import Player
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError


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
class TestWarpGateFactionRepTeamStanding:
    def test_solo_personal_rep_unchanged(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        faction = SimpleNamespace(
            id=faction_id, faction_type=FactionType.FEDERATION
        )

        def _resolve(db, pid, fid, *, team_id=None):
            assert pid == player_id
            assert fid == faction_id
            return 10, "personal"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )
        value = warp_gate_service._faction_rep_value(
            _FakeDb(faction=faction), player_id, "Federation"
        )
        assert value == 10

    def test_teamed_player_uses_team_aggregate(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        faction = SimpleNamespace(
            id=faction_id, faction_type=FactionType.FEDERATION
        )
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (80, "team"),
        )
        value = warp_gate_service._faction_rep_value(
            _FakeDb(faction=faction), player_id, "Federation"
        )
        assert value == 80

    def test_rep_min_layer_opens_via_team_average(self, monkeypatch):
        player = Player(id=uuid4(), team_id=uuid4())
        faction = SimpleNamespace(
            id=uuid4(), faction_type=FactionType.FEDERATION
        )
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (80, "team"),
        )
        # Would fail if personal-only at 10; team AVERAGE 80 clears threshold 50.
        warp_gate_service._check_faction_rep_layers(
            _FakeDb(faction=faction),
            player,
            {"faction_rep_min": {"faction_type": "Federation", "value": 50}},
        )

    def test_rep_min_layer_denies_when_team_below_threshold(self, monkeypatch):
        player = Player(id=uuid4(), team_id=uuid4())
        faction = SimpleNamespace(
            id=uuid4(), faction_type=FactionType.FEDERATION
        )
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (10, "team"),
        )
        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service._check_faction_rep_layers(
                _FakeDb(faction=faction),
                player,
                {"faction_rep_min": {"faction_type": "Federation", "value": 50}},
            )
        assert exc_info.value.status_code == 403
        assert "ERR_GATE_REP_TOO_LOW" in str(exc_info.value)

    def test_missing_faction_row_resolves_to_zero(self):
        value = warp_gate_service._faction_rep_value(
            _FakeDb(faction=None), uuid4(), "Federation"
        )
        assert value == 0
