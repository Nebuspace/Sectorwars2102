"""WO-BUILD-COMBAT-WEAPON-CATALOG-AUTOCANNON-PARTICLE-TORPEDO

Pins: planned autocannon/particle/torpedo profiles exist in WEAPON_TYPES,
equipment mounts declare weapon_type-only effects (§2.6 — no raw firepower),
and CombatService._weapon_key_for_ship prefers equipment over hull default.
"""
from __future__ import annotations

from src.models.ship import Ship as ShipModel
from src.models.ship import ShipType
from src.services.combat_service import CombatService
from src.services.ship_upgrade_service import ShipUpgradeService


NEW_PROFILES = ("autocannon", "particle", "torpedo")
MOUNT_KEYS = {
    "autocannon": "autocannon_mount",
    "particle": "particle_projector",
    "torpedo": "torpedo_bay",
}


def _ship(*, ship_type=ShipType.LIGHT_FREIGHTER, equipment_slots=None):
    ship = ShipModel()
    ship.type = ship_type
    ship.equipment_slots = equipment_slots or {}
    ship.modules = None
    return ship


def _cs():
    return CombatService(None)


class TestWeaponCatalogProfiles:
    def test_new_profiles_present_with_required_keys(self):
        for key in NEW_PROFILES:
            assert key in CombatService.WEAPON_TYPES
            profile = CombatService.WEAPON_TYPES[key]
            for field in ("base_damage", "shield_effectiveness", "hull_effectiveness", "description"):
                assert field in profile

    def test_equipment_mounts_set_weapon_type_only(self):
        for profile, mount_key in MOUNT_KEYS.items():
            assert mount_key in ShipUpgradeService.EQUIPMENT_DEFINITIONS
            effects = ShipUpgradeService.EQUIPMENT_DEFINITIONS[mount_key]["effects"]
            assert effects == {"weapon_type": profile}
            assert "weapon_damage" not in effects
            assert "attack_rating" not in effects

    def test_hull_default_when_no_mount(self):
        # LIGHT_FREIGHTER → laser; DEFENDER → plasma (SHIP_DEFAULT_WEAPONS)
        cs = _cs()
        assert cs._weapon_key_for_ship(_ship(ship_type=ShipType.LIGHT_FREIGHTER)) == "laser"
        assert cs._weapon_key_for_ship(_ship(ship_type=ShipType.DEFENDER)) == "plasma"
        assert cs._weapon_key_for_ship(_ship(ship_type=ShipType.CARRIER)) == "missile"
        assert cs._weapon_key_for_ship(None) == "laser"

    def test_equipment_mount_overrides_hull_default(self):
        cs = _cs()
        # Freighter hull default is laser; autocannon mount switches profile.
        ship = _ship(
            ship_type=ShipType.LIGHT_FREIGHTER,
            equipment_slots={
                "autocannon_mount": {"effects": {"weapon_type": "autocannon"}},
            },
        )
        assert cs._weapon_key_for_ship(ship) == "autocannon"

        ship.equipment_slots = {
            "particle_projector": {"effects": {"weapon_type": "particle"}},
        }
        assert cs._weapon_key_for_ship(ship) == "particle"

        ship.equipment_slots = {
            "torpedo_bay": {"effects": {"weapon_type": "torpedo"}},
        }
        assert cs._weapon_key_for_ship(ship) == "torpedo"

    def test_unknown_weapon_type_falls_back_to_hull_default(self):
        cs = _cs()
        ship = _ship(
            ship_type=ShipType.DEFENDER,
            equipment_slots={
                "bogus_mount": {"effects": {"weapon_type": "not_a_real_profile"}},
            },
        )
        assert cs._weapon_key_for_ship(ship) == "plasma"
