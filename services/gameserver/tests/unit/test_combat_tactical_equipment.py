"""WO-BUILD-COMBAT-SHIP-EQUIPMENT-DEFINITIONS — ECM / stealth tactical gear.

Canon (ship-systems.md §2.6): combat equipment is tactical modifiers
(ECM, stealth), NEVER raw firepower. attack_rating stays hull-fixed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models.ship import ShipType
from src.services.combat_service import CombatService
from src.services.ship_upgrade_service import ShipUpgradeService


@pytest.mark.unit
class TestCombatEquipmentCatalog:
    def test_ecm_and_stealth_in_equipment_definitions(self) -> None:
        defs = ShipUpgradeService.EQUIPMENT_DEFINITIONS
        assert "ecm_suite" in defs
        assert "stealth_module" in defs
        assert defs["ecm_suite"]["effects"]["ecm_hit_penalty"] == 0.15
        assert defs["stealth_module"]["effects"]["stealth_evasion_bonus"] == 15
        # No raw firepower keys — docs-win vs WO's "weapon-boost" wording.
        for key in ("ecm_suite", "stealth_module"):
            effects = defs[key]["effects"]
            assert "attack_rating" not in effects
            assert "weapon_damage" not in effects
            assert "attack_bonus" not in effects

    def test_compatible_ships_are_combat_utility_hulls(self) -> None:
        ecm_ships = set(ShipUpgradeService.EQUIPMENT_DEFINITIONS["ecm_suite"]["compatible_ships"])
        stealth_ships = set(
            ShipUpgradeService.EQUIPMENT_DEFINITIONS["stealth_module"]["compatible_ships"]
        )
        assert ShipType.DEFENDER in ecm_ships
        assert ShipType.SCOUT_SHIP in stealth_ships
        assert ShipType.FAST_COURIER in stealth_ships


@pytest.mark.unit
class TestEcmHitPenalty:
    def test_ecm_reduces_hit_chance(self) -> None:
        svc = CombatService(MagicMock())
        ship = SimpleNamespace(
            equipment_slots={
                "ecm_suite": {
                    "effects": {"ecm_hit_penalty": 0.15},
                }
            },
            modules=None,
        )
        assert svc._apply_defender_ecm(0.80, ship) == pytest.approx(0.80 * 0.85)

    def test_no_ecm_passthrough(self) -> None:
        svc = CombatService(MagicMock())
        ship = SimpleNamespace(equipment_slots={}, modules=None)
        assert svc._apply_defender_ecm(0.50, ship) == 0.50

    def test_none_ship_passthrough(self) -> None:
        svc = CombatService(MagicMock())
        assert svc._apply_defender_ecm(0.50, None) == 0.50


@pytest.mark.unit
class TestStealthDefenseBonus:
    def test_stealth_raises_defense(self) -> None:
        svc = CombatService(MagicMock())
        bare = SimpleNamespace(
            type=ShipType.SCOUT_SHIP,
            combat={},
            equipment_slots={},
            modules=None,
            maintenance=None,
        )
        stealthed = SimpleNamespace(
            type=ShipType.SCOUT_SHIP,
            combat={},
            equipment_slots={
                "stealth_module": {"effects": {"stealth_evasion_bonus": 15}},
            },
            modules=None,
            maintenance=None,
        )
        from src.services.maintenance_service import combat_multiplier

        bare_def = svc._calculate_defense_power(bare, drones=0)
        stealthed_def = svc._calculate_defense_power(stealthed, drones=0)
        expected_delta = 15 * combat_multiplier(stealthed)
        assert stealthed_def - bare_def == pytest.approx(expected_delta)
