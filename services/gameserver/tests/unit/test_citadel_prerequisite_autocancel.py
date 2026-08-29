"""Tests for the ADR-0059 N-F3/N-V5 prerequisite-loss auto-cancel gap
(backlog item P5-planets-citadel-autocancel).

citadel_service already implemented N-D4 (the specific-building prerequisite
scan replacing the old flat defense_level check — CITADEL_UPGRADE_PREREQS /
_check_upgrade_prereqs / _eval_prereq). What was missing per citadels.md's own
"🐛 Bug" note and the "Mid-upgrade cancellation flow" section:

  (a) CitadelService.handle_prerequisite_loss — auto-cancel + full refund +
      citadel.upgrade_cancelled event + ARIA P-F9 narration when a prereq
      building/shield a live upgrade depends on goes offline.
  (b) ERR_CITADEL_PREREQUISITE_OFFLINE surfaced in the message for a blocked
      NEW upgrade attempt against an offline prerequisite.
  (c) citadel level itself must NOT downgrade on an auto-cancel.

FakeSession mirrors test_planet_minefield_build_endpoint.py's _FakeQuery/
_FakeSession shape (route by SQLAlchemy model class), since
handle_prerequisite_loss queries both Planet and Player.
"""
import uuid
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.models.planet import Planet
from src.models.player import Player
from src.services.citadel_service import (
    CITADEL_LEVELS,
    CITADEL_UPGRADE_PREREQS,
    CitadelService,
)


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


def _planet(*, citadel_level, citadel_upgrading, defense_buildings=None,
            defense_shields=0, owner_id=None, fuel_ore=0, organics=0, equipment=0):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        citadel_upgrading=citadel_upgrading,
        citadel_upgrade_started_at=datetime.now(UTC) - timedelta(hours=1) if citadel_upgrading else None,
        citadel_upgrade_complete_at=datetime.now(UTC) + timedelta(hours=1) if citadel_upgrading else None,
        active_events={"defense_buildings": dict(defense_buildings or {})},
        defense_shields=defense_shields,
        fuel_ore=fuel_ore,
        organics=organics,
        equipment=equipment,
    )


def _player(*, credits=0, owner_id=None):
    return SimpleNamespace(id=owner_id or uuid.uuid4(), user_id=uuid.uuid4(), credits=credits)


def _svc(planet, player):
    return CitadelService(_FakeSession(planet, player))


# --------------------------------------------------------------------------- #
# (a) Auto-cancel + full refund + event/narration on prerequisite loss.
# --------------------------------------------------------------------------- #

def test_prerequisite_loss_cancels_upgrade_and_refunds_full_cost():
    """L3 Colony upgrade requires Defense Grid L1 OR Turret Network. Player had
    only the Turret Network; it goes offline (count drops to 0) mid-upgrade ->
    the upgrade must auto-cancel with a FULL (not 50%) refund."""
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 0},  # already lost before the call
        owner_id=owner,
    )
    player = _player(credits=0, owner_id=owner)
    svc = _svc(planet, player)

    result = svc.handle_prerequisite_loss(planet.id, "turret_network")

    assert result["success"] is True
    assert result["reason"] == "prerequisite_building_offline"
    assert result["cancelled_upgrade"] == CITADEL_LEVELS[3]["name"]
    assert result["lost_building"] == "Automated Turret Network"

    # Full refund, not the 50% player-initiated schedule.
    expected_refund = CITADEL_LEVELS[3]["upgrade_cost"]
    assert result["credits_refunded"] == expected_refund
    assert expected_refund > 0
    assert player.credits == expected_refund

    # Upgrade state cleared.
    assert planet.citadel_upgrading is False
    assert planet.citadel_upgrade_started_at is None
    assert planet.citadel_upgrade_complete_at is None


def test_prerequisite_loss_refunds_planet_resources_too():
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 0},
        owner_id=owner,
        fuel_ore=0, organics=0, equipment=0,
    )
    player = _player(credits=0, owner_id=owner)
    svc = _svc(planet, player)

    result = svc.handle_prerequisite_loss(planet.id, "turret_network")

    expected_resources = CITADEL_LEVELS[3]["resource_cost"]
    assert result["resources_refunded"] == expected_resources
    for resource, amount in expected_resources.items():
        assert getattr(planet, resource) == amount


def test_prerequisite_loss_is_noop_when_other_or_leg_still_satisfied():
    """"any" mode (L3): losing the turret network must NOT cancel the upgrade
    if the Defense Grid leg is still operational — the OR is still satisfied."""
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 0, "planetary_defense_grid": 1},
        owner_id=owner,
    )
    player = _player(credits=0, owner_id=owner)
    svc = _svc(planet, player)

    result = svc.handle_prerequisite_loss(planet.id, "turret_network")

    assert result["success"] is False
    assert planet.citadel_upgrading is True  # untouched
    assert player.credits == 0  # no refund fired


def test_prerequisite_loss_is_noop_when_no_upgrade_in_progress():
    owner = uuid.uuid4()
    planet = _planet(citadel_level=2, citadel_upgrading=False, owner_id=owner)
    player = _player(owner_id=owner)
    svc = _svc(planet, player)

    result = svc.handle_prerequisite_loss(planet.id, "turret_network")

    assert result["success"] is False
    assert "in progress" in result["message"]


# --------------------------------------------------------------------------- #
# (b) ERR_CITADEL_PREREQUISITE_OFFLINE surfaced for a blocked NEW attempt.
# --------------------------------------------------------------------------- #

def test_new_upgrade_attempt_blocked_with_error_code_when_prereq_offline():
    """A building that's present but still under construction (in the build
    queue, not yet operational) is "offline", not "missing" -- and the
    returned message must carry ERR_CITADEL_PREREQUISITE_OFFLINE."""
    owner = uuid.uuid4()
    planet = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner,
        citadel_level=2,
        active_events={
            "defense_buildings": {},
            # Both L3 "any"-mode legs queued (under construction, not missing)
            # so the "prefer missing over offline" tie-break in
            # _check_upgrade_prereqs doesn't mask the offline reason we're
            # asserting on here.
            "defense_build_queue": [
                {"type": "turret_network", "started_at": "x", "complete_at": "y"},
                {"type": "planetary_defense_grid", "started_at": "x", "complete_at": "y"},
            ],
        },
        defense_shields=0,
    )
    svc = CitadelService(_FakeSession(planet, None))

    failure = svc._check_upgrade_prereqs(planet, 3)

    assert failure is not None
    assert failure["reason"] == "prerequisite_building_offline"
    assert failure["error_code"] == "ERR_CITADEL_PREREQUISITE_OFFLINE"
    assert "ERR_CITADEL_PREREQUISITE_OFFLINE" in failure["message"]
    assert "Defense Grid L1" in failure["message"]  # first "any"-mode requirement, both legs offline


def test_new_upgrade_attempt_blocked_with_error_code_when_prereq_missing():
    """A building that is absent (not in buildings or queue) is "missing" --
    returned payload must carry ERR_CITADEL_PREREQUISITE_MISSING plus building identity."""
    owner = uuid.uuid4()
    planet = SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner,
        citadel_level=3,
        active_events={
            "defense_buildings": {
                "planetary_defense_grid": 2,
                "turret_network": 1,
            },
            "defense_build_queue": [],
        },
        defense_shields=0,  # Shield Generator L4 required for L4, absent
    )
    svc = CitadelService(_FakeSession(planet, None))

    failure = svc._check_upgrade_prereqs(planet, 4)

    assert failure is not None
    assert failure["reason"] == "prerequisite_building_missing"
    assert failure["error_code"] == "ERR_CITADEL_PREREQUISITE_MISSING"
    assert "ERR_CITADEL_PREREQUISITE_MISSING" in failure["message"]
    assert failure["building_key"] == "shield_generator"
    assert "Shield Generator L4" in failure["building_name"]


# --------------------------------------------------------------------------- #
# (c) Citadel level does NOT downgrade on auto-cancel.
# --------------------------------------------------------------------------- #

def test_citadel_level_unchanged_by_prerequisite_loss_autocancel():
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=2,
        citadel_upgrading=True,
        defense_buildings={"turret_network": 0},
        owner_id=owner,
    )
    player = _player(credits=0, owner_id=owner)
    svc = _svc(planet, player)

    svc.handle_prerequisite_loss(planet.id, "turret_network")

    # Level 2 (Colony's prerequisite, still working toward L3) is preserved --
    # the upgrade to L3 was cancelled, the planet did NOT drop below L2.
    assert planet.citadel_level == 2


def test_l4_all_mode_loss_of_any_single_required_building_cancels():
    """L4 is "all" mode: losing ANY one of its required buildings must cancel
    (unlike L3's "any" mode)."""
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=3,
        citadel_upgrading=True,
        defense_buildings={
            "planetary_defense_grid": 1,  # dropped below the required 2
            "turret_network": 1,
        },
        defense_shields=4,
        owner_id=owner,
    )
    player = _player(credits=0, owner_id=owner)
    svc = _svc(planet, player)

    result = svc.handle_prerequisite_loss(planet.id, "planetary_defense_grid", "Defense Grid L2")

    assert result["success"] is True
    assert result["cancelled_upgrade"] == CITADEL_LEVELS[4]["name"]
    assert planet.citadel_level == 3  # unchanged
