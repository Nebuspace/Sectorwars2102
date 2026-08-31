"""LEG-3147 — grid decommission wires citadel handle_prerequisite_loss."""
import uuid
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.planet import Planet
from src.models.player import Player
from src.api.routes.planet_grid import _apply_ct1_defense_decommission
from src.services.citadel_service import CitadelService


class _FakeQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._obj

    def scalar(self):
        return getattr(self._obj, "user_id", None) if self._obj is not None else None


class _FakeSession:
    def __init__(self, planet, player):
        self._planet = planet
        self._player = player
        self.flush_count = 0

    def query(self, model):
        if model is Planet:
            return _FakeQuery(self._planet)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query model: {model}")

    def flush(self):
        self.flush_count += 1


def _planet(*, citadel_level, citadel_upgrading, defense_buildings=None, owner_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        citadel_upgrading=citadel_upgrading,
        citadel_upgrade_started_at=datetime.now(UTC) - timedelta(hours=1) if citadel_upgrading else None,
        citadel_upgrade_complete_at=datetime.now(UTC) + timedelta(hours=1) if citadel_upgrading else None,
        active_events={"defense_buildings": dict(defense_buildings or {})},
        defense_shields=0,
        fuel_ore=0,
        organics=0,
        equipment=0,
    )


def _player(credits=500_000):
    return SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), credits=credits)


def test_decommission_triggers_prerequisite_loss_cancel():
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 1},
    )
    player = _player()
    planet.owner_id = player.id
    db = _FakeSession(planet, player)

    mutated = _apply_ct1_defense_decommission(planet, "TURRET_NETWORK", db)

    assert mutated is True
    assert planet.active_events["defense_buildings"].get("turret_network", 0) == 0
    assert planet.citadel_upgrading is False


def test_decommission_no_cancel_when_prereqs_still_satisfied():
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 2, "planetary_defense_grid": 1},
    )
    player = _player()
    planet.owner_id = player.id
    db = _FakeSession(planet, player)

    mutated = _apply_ct1_defense_decommission(planet, "TURRET_NETWORK", db)

    assert mutated is True
    assert planet.active_events["defense_buildings"]["turret_network"] == 1
    assert planet.citadel_upgrading is True


def test_non_ct1_kind_does_not_call_handle_prerequisite_loss():
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={},
    )
    db = MagicMock()

    with patch.object(CitadelService, "handle_prerequisite_loss") as mock_loss:
        mutated = _apply_ct1_defense_decommission(planet, "MINE", db)

    assert mutated is False
    mock_loss.assert_not_called()
