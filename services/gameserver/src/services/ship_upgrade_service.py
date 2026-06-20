"""
Ship Upgrade Service
Handles ship upgrades (engine, cargo, shields, etc.) and equipment installation.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipType, ShipSpecification, UpgradeType

logger = logging.getLogger(__name__)


class ShipUpgradeService:
    """Service for managing ship upgrades and equipment installations"""

    UPGRADE_DEFINITIONS = {
        UpgradeType.ENGINE: {
            "base_cost": 5000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"speed_bonus": 0.5},
            "description": "Improves ship speed by +0.5 per level"
        },
        UpgradeType.CARGO_HOLD: {
            "base_cost": 3000,
            "cost_multiplier": 1.8,
            "effect_per_level": {"cargo_bonus_percent": 30},
            "description": "Increases cargo capacity by +30% per level"
        },
        UpgradeType.SHIELD: {
            "base_cost": 8000,
            "cost_multiplier": 2.2,
            "effect_per_level": {"shield_bonus": 200},
            "description": "Increases max shields by +200 per level"
        },
        UpgradeType.HULL: {
            "base_cost": 7000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"hull_bonus": 300},
            "description": "Increases hull points by +300 per level"
        },
        UpgradeType.SENSOR: {
            "base_cost": 6000,
            "cost_multiplier": 2.5,
            # Canon (sw2102-docs ship-systems.md §2.5): "Each Sensor level adds
            # +15% evasion. Sensors also affect scan range." The evasion number
            # is canon; the scan-range increment is NO-CANON (the doc marks the
            # scan-range effect 📐 Design-only with no per-level figure). Kernel:
            # +1 scanner-range sector per Sensor level — flagged for a
            # DECISIONS.md Pending ruling. The effective scanner range
            # (spec base + this bonus) is computed by effective_scanner_range();
            # there is no per-instance scanner_range column to mutate, so the
            # bonus is applied as a derived value the scan path consults.
            "effect_per_level": {"evasion_bonus_percent": 15, "scanner_range_bonus": 1},
            "description": "Increases evasion by +15% per level and scan range by +1 sector per level"
        },
        UpgradeType.DRONE_BAY: {
            "base_cost": 10000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"drone_capacity_bonus": 2},
            "description": "Increases drone capacity by +2 per level"
        },
        UpgradeType.GENESIS_CONTAINMENT: {
            "base_cost": 15000,
            "cost_multiplier": 3.0,
            "effect_per_level": {"genesis_capacity_bonus": 2},
            "description": "Increases genesis device capacity by +2 per level"
        },
        # NO-CANON kernel (sw2102-docs ship-systems.md §2.9 marks cost/effect 📐 Design-only).
        # Cost scaling mirrors the Hull/Sensor utility tier (base 6,000, x2.0). Effect:
        # each level reduces the ship's mechanical failure rate by 0.15 (15% relative)
        # of the spec's base maintenance_rate, applied via _apply_upgrade_effects into the
        # maintenance JSONB. Numbers flagged for a DECISIONS Pending entry.
        UpgradeType.MAINTENANCE_SYSTEM: {
            "base_cost": 6000,
            "cost_multiplier": 2.0,
            "effect_per_level": {"failure_rate_reduction": 0.15},
            "description": "Reduces mechanical failure rate by 15% per level"
        },
    }

    EQUIPMENT_DEFINITIONS = {
        "quantum_harvester": {
            "name": "Quantum Harvester",
            "description": "Harvests quantum particles from space, providing passive income",
            "cost": 25000,
            "compatible_ships": [ShipType.SCOUT_SHIP, ShipType.FAST_COURIER, ShipType.DEFENDER, ShipType.WARP_JUMPER],
            "effects": {"passive_income": 100}
        },
        "mining_laser": {
            "name": "Mining Laser",
            "description": "Allows direct mining of asteroid fields for resources",
            "cost": 35000,
            "compatible_ships": [ShipType.CARGO_HAULER, ShipType.COLONY_SHIP, ShipType.DEFENDER],
            "effects": {"mining_efficiency": 1.5}
        },
        "planetary_lander": {
            "name": "Planetary Lander",
            "description": "Advanced landing module for improved planet interaction",
            "cost": 20000,
            "compatible_ships": [ShipType.COLONY_SHIP, ShipType.LIGHT_FREIGHTER, ShipType.CARGO_HAULER],
            "effects": {"landing_bonus": 1.25}
        },
    }

    # NO-CANON kernel (ship-systems.md §2.5 marks the Sensor scan-range effect
    # 📐 Design-only): each Sensor upgrade level adds +1 sector of scanner range
    # on top of the hull spec's base scanner_range. Flagged for a DECISIONS.md
    # Pending ruling on the exact per-level figure.
    SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL = 1

    @staticmethod
    def get_sensor_level(ship) -> int:
        """Read the ship's current Sensor upgrade level from its upgrades JSONB."""
        upgrades = getattr(ship, "upgrades", None)
        if not isinstance(upgrades, dict):
            return 0
        try:
            return int(upgrades.get(UpgradeType.SENSOR.value, 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def effective_scanner_range(ship, base_scanner_range: int) -> int:
        """Effective scanner range = the hull spec's base scanner_range plus the
        Sensor-upgrade scan-range bonus (+1 sector per Sensor level, NO-CANON
        kernel — see SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL).

        `Ship` has no per-instance scanner_range column (the value lives on
        `ShipSpecification.scanner_range`); callers pass that spec base in and
        the scan path consults the returned effective value.
        """
        sensor_level = ShipUpgradeService.get_sensor_level(ship)
        bonus = sensor_level * ShipUpgradeService.SCANNER_RANGE_BONUS_PER_SENSOR_LEVEL
        return int(base_scanner_range) + bonus

    @staticmethod
    def get_passive_income(ship) -> int:
        """Total per-period passive_income a ship's installed equipment grants.

        Read-only. Authoritative source is EQUIPMENT_DEFINITIONS keyed by the
        equipment actually installed in the ship's equipment_slots JSONB — NOT
        the effects snapshot stored on the slot at install time — so a future
        re-tuning of the canonical passive_income figure (e.g. a DECISIONS.md
        ruling) takes effect for already-equipped ships without a backfill. If a
        ship carries the effect via MULTIPLE equipment sources, their
        passive_income values are SUMMED. Returns 0 when the ship carries no
        passive_income equipment (the common case), so the idle-income sweep
        skips it cleanly.

        Used by npc_scheduler_service's daily idle-income credit-grant sweep
        (ship-systems.md §passive_income: "applied per-tick by an idle-income
        job"). Magnitude/cadence are NO-CANON (the doc marks the effect
        📐 Design-only) — flagged for the orchestrator.
        """
        equipment_slots = getattr(ship, "equipment_slots", None) or {}
        total = 0
        for eq_key in equipment_slots.keys():
            eq_def = ShipUpgradeService.EQUIPMENT_DEFINITIONS.get(eq_key)
            if not eq_def:
                continue
            value = eq_def.get("effects", {}).get("passive_income")
            if isinstance(value, (int, float)):
                total += int(value)
        return total

    @staticmethod
    def get_equipment_effects(ship) -> Dict[str, Any]:
        """Read equipment_slots JSONB and return a merged dict of all active effects.

        Example return: {"passive_income": 100, "mining_efficiency": 1.5}
        Services can call this to apply bonuses from installed equipment.
        """
        equipment_slots = getattr(ship, 'equipment_slots', None) or {}
        merged: Dict[str, Any] = {}
        for eq_key, eq_data in equipment_slots.items():
            effects = eq_data.get("effects", {}) if isinstance(eq_data, dict) else {}
            for effect_name, effect_value in effects.items():
                if effect_name in merged:
                    # Additive stacking for numeric effects
                    if isinstance(effect_value, (int, float)) and isinstance(merged[effect_name], (int, float)):
                        merged[effect_name] += effect_value
                    else:
                        merged[effect_name] = effect_value
                else:
                    merged[effect_name] = effect_value
        return merged

    def __init__(self, db: Session):
        self.db = db

    def _get_ship_and_player(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> tuple:
        """Fetch and validate ship ownership. Returns (ship, player, error_dict).
        Locks the player row to prevent concurrent purchase race conditions."""
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return None, None, {"success": False, "message": "Player not found"}

        ship = self.db.query(Ship).filter(Ship.id == ship_id).with_for_update().first()
        if not ship:
            return None, None, {"success": False, "message": "Ship not found"}

        if ship.owner_id != player_id:
            return None, None, {"success": False, "message": "You do not own this ship"}

        if ship.is_destroyed:
            return None, None, {"success": False, "message": "Cannot modify a destroyed ship"}

        return ship, player, None

    def _get_current_upgrade_level(self, ship: Ship, upgrade_type: UpgradeType) -> int:
        """Get the current upgrade level for a given type from the ship's upgrades JSONB."""
        upgrades = ship.upgrades
        if not upgrades or not isinstance(upgrades, dict):
            return 0
        return upgrades.get(upgrade_type.value, 0)

    def _get_max_upgrade_level(self, ship: Ship, upgrade_type: UpgradeType) -> int:
        """Get the max upgrade level for a given type from the ship's specification."""
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()
        if not spec or not spec.max_upgrade_levels:
            return 0
        return spec.max_upgrade_levels.get(upgrade_type.value, 0)

    def _calculate_upgrade_cost(self, upgrade_type: UpgradeType, current_level: int) -> int:
        """Calculate the cost for the next upgrade level."""
        definition = self.UPGRADE_DEFINITIONS[upgrade_type]
        return int(definition["base_cost"] * (definition["cost_multiplier"] ** current_level))

    def get_upgrade_info(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """
        Returns current upgrade levels, max levels, and costs for next upgrade
        for each category, plus equipped equipment slots.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()

        upgrade_info = {}
        for upgrade_type, definition in self.UPGRADE_DEFINITIONS.items():
            current_level = self._get_current_upgrade_level(ship, upgrade_type)
            max_level = spec.max_upgrade_levels.get(upgrade_type.value, 0) if spec and spec.max_upgrade_levels else 0
            at_max = current_level >= max_level

            upgrade_info[upgrade_type.value] = {
                "current_level": current_level,
                "max_level": max_level,
                "at_max": at_max,
                "next_cost": self._calculate_upgrade_cost(upgrade_type, current_level) if not at_max else None,
                "effect_per_level": definition["effect_per_level"],
                "description": definition["description"],
            }

        # Equipment slots
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}

        # Available equipment for this ship type
        available_equipment = {}
        for eq_key, eq_def in self.EQUIPMENT_DEFINITIONS.items():
            compatible = ship.type in eq_def["compatible_ships"]
            installed = eq_key in equipment_slots
            available_equipment[eq_key] = {
                "name": eq_def["name"],
                "description": eq_def["description"],
                "cost": eq_def["cost"],
                "compatible": compatible,
                "installed": installed,
                "effects": eq_def["effects"],
            }

        return {
            "success": True,
            "ship_id": str(ship.id),
            "ship_name": ship.name,
            "ship_type": ship.type.value,
            "upgrades": upgrade_info,
            "equipment": available_equipment,
            "equipped": equipment_slots,
            "player_credits": player.credits,
        }

    def purchase_upgrade(self, ship_id: uuid.UUID, player_id: uuid.UUID, upgrade_type: UpgradeType) -> Dict[str, Any]:
        """
        Purchase an upgrade for a ship. Validates ownership, level limits, and credits.
        Applies stat changes to the ship.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        if upgrade_type not in self.UPGRADE_DEFINITIONS:
            return {"success": False, "message": f"Unknown upgrade type: {upgrade_type}"}

        current_level = self._get_current_upgrade_level(ship, upgrade_type)
        max_level = self._get_max_upgrade_level(ship, upgrade_type)

        if max_level == 0:
            return {
                "success": False,
                "message": f"This ship type cannot be upgraded with {upgrade_type.value}"
            }

        if current_level >= max_level:
            return {
                "success": False,
                "message": f"{upgrade_type.value} is already at maximum level ({max_level})"
            }

        cost = self._calculate_upgrade_cost(upgrade_type, current_level)

        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }

        # Deduct credits
        player.credits -= cost

        # Increment upgrade level in ship's upgrades JSONB
        if not ship.upgrades or not isinstance(ship.upgrades, dict):
            ship.upgrades = {}
        ship.upgrades[upgrade_type.value] = current_level + 1
        flag_modified(ship, 'upgrades')

        new_level = current_level + 1
        definition = self.UPGRADE_DEFINITIONS[upgrade_type]
        effects = definition["effect_per_level"]

        # Apply stat changes based on upgrade type
        updated_stats = self._apply_upgrade_effects(ship, upgrade_type, effects)

        self.db.flush()

        logger.info(
            f"Player {player_id} upgraded {upgrade_type.value} to level {new_level} "
            f"on ship {ship.name} for {cost:,} credits"
        )

        return {
            "success": True,
            "message": f"{upgrade_type.value} upgraded to level {new_level}",
            "upgrade_type": upgrade_type.value,
            "new_level": new_level,
            "max_level": max_level,
            "cost_paid": cost,
            "remaining_credits": player.credits,
            "updated_stats": updated_stats,
        }

    def _apply_upgrade_effects(self, ship: Ship, upgrade_type: UpgradeType, effects: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the stat changes for an upgrade to the ship and return a summary of changes."""
        updated = {}

        if upgrade_type == UpgradeType.ENGINE:
            speed_bonus = effects["speed_bonus"]
            ship.current_speed += speed_bonus
            updated["current_speed"] = ship.current_speed

        elif upgrade_type == UpgradeType.CARGO_HOLD:
            # Cargo capacity is stored in the cargo JSONB or derived from spec;
            # we store a cargo_capacity_bonus in cargo JSONB for the service layer to use.
            if not ship.cargo or not isinstance(ship.cargo, dict):
                ship.cargo = {}
            current_bonus = ship.cargo.get("_capacity_bonus_percent", 0)
            ship.cargo["_capacity_bonus_percent"] = current_bonus + effects["cargo_bonus_percent"]
            flag_modified(ship, 'cargo')
            updated["cargo_capacity_bonus_percent"] = ship.cargo["_capacity_bonus_percent"]

        elif upgrade_type == UpgradeType.SHIELD:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            shield_bonus = effects["shield_bonus"]
            combat["max_shields"] = combat.get("max_shields", 0) + shield_bonus
            combat["shields"] = combat.get("shields", 0) + shield_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_shields"] = combat["max_shields"]
            updated["shields"] = combat["shields"]

        elif upgrade_type == UpgradeType.HULL:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            hull_bonus = effects["hull_bonus"]
            combat["max_hull"] = combat.get("max_hull", 0) + hull_bonus
            combat["hull"] = combat.get("hull", 0) + hull_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["max_hull"] = combat["max_hull"]
            updated["hull"] = combat["hull"]

        elif upgrade_type == UpgradeType.SENSOR:
            combat = ship.combat if isinstance(ship.combat, dict) else {}
            evasion_bonus = effects["evasion_bonus_percent"]
            base_evasion = combat.get("evasion", 0)
            combat["evasion"] = base_evasion + evasion_bonus
            ship.combat = combat
            flag_modified(ship, 'combat')
            updated["evasion"] = combat["evasion"]
            # Scan-range half of the Sensor upgrade (canon ship-systems.md §2.5;
            # NO-CANON per-level figure). `Ship` has no scanner_range column, so
            # the effective value is derived from the hull spec's base
            # scanner_range plus the (now incremented) Sensor level. Reported so
            # the upgrade UI / scan path can surface the wider reach.
            spec = self.db.query(ShipSpecification).filter(
                ShipSpecification.type == ship.type
            ).first()
            base_scanner_range = spec.scanner_range if spec and spec.scanner_range is not None else 0
            updated["scanner_range"] = self.effective_scanner_range(ship, base_scanner_range)

        elif upgrade_type == UpgradeType.DRONE_BAY:
            drone_bonus = effects["drone_capacity_bonus"]
            # Drone capacity is not a direct column on Ship; store in upgrades JSONB
            # which is already handled. The service layer reads max from spec + upgrades.
            updated["drone_capacity_bonus"] = drone_bonus

        elif upgrade_type == UpgradeType.GENESIS_CONTAINMENT:
            genesis_bonus = effects["genesis_capacity_bonus"]
            ship.max_genesis_devices += genesis_bonus
            updated["max_genesis_devices"] = ship.max_genesis_devices

        elif upgrade_type == UpgradeType.MAINTENANCE_SYSTEM:
            # Accumulate a cumulative failure-rate reduction into the maintenance JSONB.
            # Stored as a fraction (0.0–1.0); the failure-roll logic multiplies the spec's
            # base maintenance_rate by (1 - failure_rate_reduction). Clamp at 1.0 so the
            # cumulative reduction can never invert the rate.
            maintenance = ship.maintenance if isinstance(ship.maintenance, dict) else {}
            reduction = effects["failure_rate_reduction"]
            current_reduction = maintenance.get("failure_rate_reduction", 0)
            maintenance["failure_rate_reduction"] = min(1.0, current_reduction + reduction)
            ship.maintenance = maintenance
            flag_modified(ship, 'maintenance')
            updated["failure_rate_reduction"] = maintenance["failure_rate_reduction"]

        return updated

    def install_equipment(self, ship_id: uuid.UUID, player_id: uuid.UUID, equipment_key: str) -> Dict[str, Any]:
        """
        Install a piece of equipment on a ship. Validates ownership, compatibility,
        slot availability, and credits.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        if equipment_key not in self.EQUIPMENT_DEFINITIONS:
            return {"success": False, "message": f"Unknown equipment: {equipment_key}"}

        eq_def = self.EQUIPMENT_DEFINITIONS[equipment_key]

        # Check ship type compatibility
        if ship.type not in eq_def["compatible_ships"]:
            compatible_names = [st.value for st in eq_def["compatible_ships"]]
            return {
                "success": False,
                "message": (
                    f"{eq_def['name']} is not compatible with {ship.type.value}. "
                    f"Compatible ships: {', '.join(compatible_names)}"
                ),
            }

        # Check if already installed
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}
        if equipment_key in equipment_slots:
            return {
                "success": False,
                "message": f"{eq_def['name']} is already installed on this ship"
            }

        # Check credits
        cost = eq_def["cost"]
        if player.credits < cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {cost:,}, have {player.credits:,}",
                "cost": cost,
                "player_credits": player.credits,
            }

        # Deduct credits
        player.credits -= cost

        # Add to equipment_slots JSONB
        if not hasattr(ship, 'equipment_slots') or not ship.equipment_slots:
            ship.equipment_slots = {}
        ship.equipment_slots[equipment_key] = {
            "installed_at": datetime.utcnow().isoformat(),
            "effects": eq_def["effects"],
        }
        flag_modified(ship, 'equipment_slots')

        self.db.flush()

        logger.info(
            f"Player {player_id} installed {eq_def['name']} on ship {ship.name} "
            f"for {cost:,} credits"
        )

        return {
            "success": True,
            "message": f"{eq_def['name']} installed successfully",
            "equipment": equipment_key,
            "cost_paid": cost,
            "remaining_credits": player.credits,
            "effects": eq_def["effects"],
        }

    def uninstall_equipment(self, ship_id: uuid.UUID, player_id: uuid.UUID, equipment_key: str) -> Dict[str, Any]:
        """
        Uninstall a piece of equipment from a ship. No credit refund.
        """
        ship, player, error = self._get_ship_and_player(ship_id, player_id)
        if error:
            return error

        # Check if equipment is installed
        equipment_slots = ship.equipment_slots if hasattr(ship, 'equipment_slots') and ship.equipment_slots else {}
        if equipment_key not in equipment_slots:
            eq_name = self.EQUIPMENT_DEFINITIONS.get(equipment_key, {}).get("name", equipment_key)
            return {
                "success": False,
                "message": f"{eq_name} is not installed on this ship"
            }

        eq_def = self.EQUIPMENT_DEFINITIONS.get(equipment_key, {})
        eq_name = eq_def.get("name", equipment_key)

        # Remove from equipment_slots JSONB
        del ship.equipment_slots[equipment_key]
        flag_modified(ship, 'equipment_slots')

        self.db.flush()

        logger.info(
            f"Player {player_id} uninstalled {eq_name} from ship {ship.name} (no refund)"
        )

        return {
            "success": True,
            "message": f"{eq_name} uninstalled (no credit refund)",
            "equipment": equipment_key,
        }
