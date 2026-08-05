"""Unit tests for WO-FLEET-SUPPORT-SCOUT-COMBAT-WIRING (Scout half)."""
from src.models.fleet import FleetRole
from src.services.fleet_service import (
    DEFENDER_ABSORPTION_MULT,
    SCOUT_DEFENSE_PENALTY_MULT,
    SCOUT_FIRST_SHOT_MULT,
    role_incoming_damage_mult,
    scout_outgoing_mult,
)


class TestScoutOutgoingMult:
    def test_scout_first_round(self):
        assert scout_outgoing_mult(FleetRole.SCOUT.value, True) == SCOUT_FIRST_SHOT_MULT

    def test_scout_later_round(self):
        assert scout_outgoing_mult(FleetRole.SCOUT.value, False) == 1.0

    def test_non_scout_first_round(self):
        assert scout_outgoing_mult(FleetRole.ATTACKER.value, True) == 1.0


class TestRoleIncomingDamageMult:
    def test_defender_absorbs(self):
        assert role_incoming_damage_mult(FleetRole.DEFENDER.value) == DEFENDER_ABSORPTION_MULT

    def test_scout_soft(self):
        assert role_incoming_damage_mult(FleetRole.SCOUT.value) == SCOUT_DEFENSE_PENALTY_MULT

    def test_support_untouched(self):
        # Support deferred — no combat multiplier yet.
        assert role_incoming_damage_mult(FleetRole.SUPPORT.value) == 1.0

    def test_none_role(self):
        assert role_incoming_damage_mult(None) == 1.0
