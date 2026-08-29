"""Ship-upgrade faction_requirements consume team standing (LEG-1907).

`check_faction_eligibility` uses `_player_reputation_level` → shared
``_player_reputation_level_for_faction`` which routes through
``resolve_effective_faction_standing_value``. Proves a teamed player can meet
a TRUSTED hull gate via team AVERAGE when personal standing alone would fail.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.faction import FactionType
from src.models.reputation import ReputationLevel
from src.api.routes import ship_upgrades


@pytest.mark.unit
class TestShipUpgradeFactionRequirementsTeamStanding:
    def test_teamed_player_meets_trusted_via_team_average(self, monkeypatch):
        player_id = uuid4()
        faction = SimpleNamespace(id=uuid4(), faction_type=FactionType.FEDERATION)
        spec = SimpleNamespace(
            faction_requirements={"terran_federation": "TRUSTED"}
        )

        class _FakeQuery:
            def filter(self, *a, **k):
                return self

            def first(self):
                return faction

        class _FakeDb:
            def query(self, model):
                return _FakeQuery()

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (200, "team"),  # TRUSTED band
        )
        ok, reason = ship_upgrades.check_faction_eligibility(
            _FakeDb(), player_id, spec
        )
        assert ok is True
        assert reason is None

    def test_teamed_player_denied_when_team_below_required(self, monkeypatch):
        player_id = uuid4()
        faction = SimpleNamespace(id=uuid4(), faction_type=FactionType.FEDERATION)
        spec = SimpleNamespace(
            faction_requirements={"terran_federation": "TRUSTED"}
        )

        class _FakeQuery:
            def filter(self, *a, **k):
                return self

            def first(self):
                return faction

        class _FakeDb:
            def query(self, model):
                return _FakeQuery()

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "team"),  # NEUTRAL — below TRUSTED
        )
        ok, reason = ship_upgrades.check_faction_eligibility(
            _FakeDb(), player_id, spec
        )
        assert ok is False
        assert reason is not None
        assert "ERR_CITIZEN_ONLY_HULL" in reason
        assert ReputationLevel.TRUSTED.value in reason or "TRUSTED" in reason

    def test_no_requirements_always_eligible(self):
        spec = SimpleNamespace(faction_requirements=None)

        class _FakeDb:
            def query(self, model):
                raise AssertionError("should not query when ungated")

        ok, reason = ship_upgrades.check_faction_eligibility(
            _FakeDb(), uuid4(), spec
        )
        assert ok is True
        assert reason is None
