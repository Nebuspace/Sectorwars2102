"""
Citadel Service - 5-level citadel upgrade system for planets.

Handles citadel progression from Outpost to Planetary Capital, timed upgrades,
resource costs, safe credit storage, and defense building construction
for planetary owners.
"""

import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.planet import Planet

logger = logging.getLogger(__name__)

# Defense buildings unlocked by citadel level progression
DEFENSE_BUILDINGS = {
    "orbital_platform": {
        "name": "Orbital Defense Platform",
        "min_citadel_level": 4,
        "max_count": {4: 1, 5: 3},
        "cost": 500000,
        "build_hours": 168,
        "effects": {"sector_range": 2, "damage_per_round": 500},
    },
    "turret_network": {
        "name": "Automated Turret Network",
        "min_citadel_level": 3,
        "max_count": {3: 2, 4: 4, 5: 6},
        "cost": 150000,
        "build_hours": 72,
        "effects": {"anti_drone_kills_per_round": 3},
    },
    "scanner_array": {
        "name": "Long-Range Scanner Array",
        "min_citadel_level": 2,
        "max_count": {2: 1, 3: 1, 4: 2, 5: 2},
        "cost": 75000,
        "build_hours": 48,
        "effects": {"detection_range_sectors": 2},
    },
}

CITADEL_LEVELS = {
    0: {
        "name": "No Citadel",
        "max_population": 0,
        "safe_storage": 0,
        "drone_capacity": 0,
        "upgrade_cost": 0,
        "upgrade_hours": 0,
        "resource_cost": {},
    },
    1: {
        "name": "Outpost",
        "max_population": 1000,
        "safe_storage": 100000,
        "drone_capacity": 10,
        "upgrade_cost": 0,
        "upgrade_hours": 0,
        "resource_cost": {},
    },
    2: {
        "name": "Settlement",
        "max_population": 5000,
        "safe_storage": 500000,
        "drone_capacity": 25,
        "upgrade_cost": 50000,
        "upgrade_hours": 48,
        "resource_cost": {"fuel_ore": 500, "equipment": 200},
    },
    3: {
        "name": "Colony",
        "max_population": 15000,
        "safe_storage": 2000000,
        "drone_capacity": 50,
        "upgrade_cost": 150000,
        "upgrade_hours": 72,
        "resource_cost": {"fuel_ore": 1500, "organics": 500, "equipment": 800},
    },
    4: {
        "name": "Major Colony",
        "max_population": 50000,
        "safe_storage": 10000000,
        "drone_capacity": 100,
        "upgrade_cost": 500000,
        "upgrade_hours": 120,
        "resource_cost": {"fuel_ore": 5000, "organics": 2000, "equipment": 3000},
    },
    5: {
        "name": "Planetary Capital",
        "max_population": 200000,
        "safe_storage": 50000000,
        "drone_capacity": 200,
        "upgrade_cost": 2000000,
        "upgrade_hours": 240,
        "resource_cost": {"fuel_ore": 15000, "organics": 8000, "equipment": 10000},
    },
}


class CitadelService:
    def __init__(self, db: Session):
        self.db = db

    def get_citadel_info(self, planet_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Get citadel information for a planet, including current level, stats, and upgrade status."""
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}

        # Check if an in-progress upgrade has completed
        self.check_upgrade_completion(planet_id)
        # Re-query to get updated state
        self.db.refresh(planet)

        current_level = getattr(planet, "citadel_level", 0) or 0
        current_info = CITADEL_LEVELS[current_level]

        result: Dict[str, Any] = {
            "success": True,
            "message": "Citadel info retrieved",
            "planet_id": str(planet_id),
            "planet_name": planet.name,
            "citadel_level": current_level,
            "citadel_name": current_info["name"],
            "max_population": current_info["max_population"],
            "safe_storage": current_info["safe_storage"],
            "safe_credits": getattr(planet, "citadel_safe_credits", 0) or 0,
            "drone_capacity": current_info["drone_capacity"],
            "is_upgrading": getattr(planet, "citadel_upgrading", False) or False,
        }

        # Include upgrade-in-progress timing info
        if getattr(planet, "citadel_upgrading", False):
            result["upgrade_started_at"] = str(planet.citadel_upgrade_started_at)
            result["upgrade_complete_at"] = planet.citadel_upgrade_complete_at.isoformat()
            remaining = planet.citadel_upgrade_complete_at - datetime.now(UTC)
            result["upgrade_remaining_seconds"] = max(0, int(remaining.total_seconds()))

        # Include next level info if not at max
        if current_level < 5:
            next_level = current_level + 1
            next_info = CITADEL_LEVELS[next_level]
            result["next_level"] = {
                "level": next_level,
                "name": next_info["name"],
                "upgrade_cost": next_info["upgrade_cost"],
                "upgrade_hours": next_info["upgrade_hours"],
                "resource_cost": next_info["resource_cost"],
                "max_population": next_info["max_population"],
                "safe_storage": next_info["safe_storage"],
                "drone_capacity": next_info["drone_capacity"],
            }
        else:
            result["next_level"] = None

        return result

    def start_upgrade(self, planet_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Start a citadel upgrade on a planet. Level 0->1 is free; higher levels cost credits and resources."""
        # Lock planet to prevent concurrent upgrade races
        planet = self.db.query(Planet).filter(Planet.id == planet_id).with_for_update().first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}

        current_level = getattr(planet, "citadel_level", 0) or 0

        if current_level >= 5:
            return {"success": False, "message": "Citadel is already at maximum level"}

        if getattr(planet, "citadel_upgrading", False):
            return {"success": False, "message": "An upgrade is already in progress"}

        next_level = current_level + 1
        next_info = CITADEL_LEVELS[next_level]

        # Prerequisite validation: higher citadel levels require planetary defenses
        prerequisite_defense = {
            3: (2, "basic defenses (defense level 2+)"),
            4: (5, "advanced defenses (defense level 5+)"),
            5: (8, "fortified defenses (defense level 8+)"),
        }
        if next_level in prerequisite_defense:
            required_defense, description = prerequisite_defense[next_level]
            planet_defense = getattr(planet, "defense_level", 0) or 0
            if planet_defense < required_defense:
                return {
                    "success": False,
                    "message": f"Requires defense level {required_defense}+ to upgrade to {next_info['name']}. "
                               f"Current defense level: {planet_defense}.",
                }

        # Level 0 -> 1 is free: apply immediately
        if current_level == 0:
            planet.citadel_level = 1
            level_1_info = CITADEL_LEVELS[1]
            planet.citadel_safe_max = level_1_info["safe_storage"]
            planet.citadel_drone_capacity = level_1_info["drone_capacity"]
            planet.citadel_max_population = level_1_info["max_population"]
            self.db.flush()
            logger.info(f"Planet {planet_id} citadel established at level 1 (Outpost) for player {player_id}")
            return {
                "success": True,
                "message": "Outpost established! Your citadel is now level 1.",
                "citadel_level": 1,
                "citadel_name": level_1_info["name"],
            }

        # For levels 1+: lock player row to prevent concurrent credit races
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return {"success": False, "message": "Player not found"}

        upgrade_cost = next_info["upgrade_cost"]
        if player.credits < upgrade_cost:
            return {
                "success": False,
                "message": f"Insufficient credits. Need {upgrade_cost:,}, have {player.credits:,}.",
            }

        # Check resource costs on the planet
        resource_cost = next_info["resource_cost"]
        for resource, amount in resource_cost.items():
            planet_resource = getattr(planet, resource, 0) or 0
            if planet_resource < amount:
                return {
                    "success": False,
                    "message": f"Insufficient {resource} on planet. Need {amount:,}, have {planet_resource:,}.",
                }

        # Deduct credits from player
        player.credits -= upgrade_cost

        # Deduct resources from planet
        for resource, amount in resource_cost.items():
            current_value = getattr(planet, resource, 0) or 0
            setattr(planet, resource, current_value - amount)

        # Start the upgrade timer
        now = datetime.now(UTC)
        upgrade_hours = next_info["upgrade_hours"]
        planet.citadel_upgrading = True
        planet.citadel_upgrade_started_at = now
        planet.citadel_upgrade_complete_at = now + timedelta(hours=upgrade_hours)

        self.db.flush()

        logger.info(
            f"Planet {planet_id} citadel upgrade started: level {current_level} -> {next_level} "
            f"({upgrade_hours}h) for player {player_id}"
        )

        return {
            "success": True,
            "message": f"Upgrade to {next_info['name']} started! Completion in {upgrade_hours} hours.",
            "citadel_level": current_level,
            "upgrading_to": next_level,
            "upgrading_to_name": next_info["name"],
            "upgrade_started_at": str(now),
            "upgrade_complete_at": (now + timedelta(hours=upgrade_hours)).isoformat(),
            "upgrade_hours": upgrade_hours,
            "credits_deducted": upgrade_cost,
            "resources_deducted": resource_cost,
        }

    def check_upgrade_completion(self, planet_id: uuid.UUID) -> Dict[str, Any]:
        """Check if an in-progress citadel upgrade has completed, and apply it if so."""
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if not getattr(planet, "citadel_upgrading", False):
            current_level = getattr(planet, "citadel_level", 0) or 0
            return {
                "success": True,
                "message": "No upgrade in progress",
                "citadel_level": current_level,
                "citadel_name": CITADEL_LEVELS[current_level]["name"],
                "is_upgrading": False,
            }

        now = datetime.now(UTC)
        if now >= planet.citadel_upgrade_complete_at:
            # Upgrade complete - apply it
            current_level = getattr(planet, "citadel_level", 0) or 0
            new_level = current_level + 1
            new_info = CITADEL_LEVELS[new_level]

            planet.citadel_level = new_level
            planet.citadel_safe_max = new_info["safe_storage"]
            planet.citadel_drone_capacity = new_info["drone_capacity"]
            planet.citadel_max_population = new_info["max_population"]
            planet.citadel_upgrading = False
            planet.citadel_upgrade_started_at = None
            planet.citadel_upgrade_complete_at = None

            self.db.flush()

            logger.info(
                f"Planet {planet_id} citadel upgrade completed: now level {new_level} ({new_info['name']})"
            )

            return {
                "success": True,
                "message": f"Upgrade complete! Citadel is now level {new_level} ({new_info['name']}).",
                "citadel_level": new_level,
                "citadel_name": new_info["name"],
                "is_upgrading": False,
                "just_completed": True,
            }
        else:
            # Still upgrading
            remaining = planet.citadel_upgrade_complete_at - now
            current_level = getattr(planet, "citadel_level", 0) or 0
            return {
                "success": True,
                "message": "Upgrade still in progress",
                "citadel_level": current_level,
                "citadel_name": CITADEL_LEVELS[current_level]["name"],
                "is_upgrading": True,
                "upgrade_complete_at": planet.citadel_upgrade_complete_at.isoformat(),
                "upgrade_remaining_seconds": max(0, int(remaining.total_seconds())),
            }

    def deposit_to_safe(self, planet_id: uuid.UUID, player_id: uuid.UUID, amount: int) -> Dict[str, Any]:
        """Deposit credits from a player's balance into the citadel's safe storage."""
        if amount <= 0:
            return {"success": False, "message": "Deposit amount must be positive"}

        # Lock planet row first, then player row (same order as start_upgrade)
        # to prevent concurrent credit-minting races on safe deposits/withdrawals.
        planet = (
            self.db.query(Planet)
            .filter(Planet.id == planet_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}

        current_level = getattr(planet, "citadel_level", 0) or 0
        if current_level < 1:
            return {"success": False, "message": "Planet does not have a citadel"}

        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not player:
            return {"success": False, "message": "Player not found"}

        if player.credits < amount:
            return {
                "success": False,
                "message": f"Insufficient credits. Have {player.credits:,}, need {amount:,}.",
            }

        # Use CITADEL_LEVELS config as authoritative source for safe storage capacity
        capacity = CITADEL_LEVELS[current_level]["safe_storage"]
        safe_current = getattr(planet, "citadel_safe_credits", 0) or 0

        if safe_current + amount > capacity:
            return {
                "success": False,
                "message": f"Safe storage capacity is {capacity:,}. Currently storing {safe_current:,}.",
            }

        player.credits -= amount
        planet.citadel_safe_credits = safe_current + amount

        self.db.flush()

        logger.info(
            f"Player {player_id} deposited {amount:,} credits into citadel safe on planet {planet_id}"
        )

        return {
            "success": True,
            "message": f"Deposited {amount:,} credits into citadel safe.",
            "credits_deposited": amount,
            "safe_balance": safe_current + amount,
            "safe_capacity": capacity,
            "player_credits": player.credits,
        }

    def withdraw_from_safe(self, planet_id: uuid.UUID, player_id: uuid.UUID, amount: int) -> Dict[str, Any]:
        """Withdraw credits from the citadel's safe storage into the player's balance."""
        if amount <= 0:
            return {"success": False, "message": "Withdrawal amount must be positive"}

        # Lock planet row first, then player row (same order as start_upgrade)
        # to prevent concurrent credit-minting races on safe deposits/withdrawals.
        planet = (
            self.db.query(Planet)
            .filter(Planet.id == planet_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}

        current_level = getattr(planet, "citadel_level", 0) or 0
        if current_level < 1:
            return {"success": False, "message": "Planet does not have a citadel"}

        safe_current = getattr(planet, "citadel_safe_credits", 0) or 0
        if safe_current < amount:
            return {
                "success": False,
                "message": f"Insufficient credits in safe. Have {safe_current:,}, requested {amount:,}.",
            }

        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not player:
            return {"success": False, "message": "Player not found"}

        planet.citadel_safe_credits = safe_current - amount
        player.credits += amount

        self.db.flush()

        logger.info(
            f"Player {player_id} withdrew {amount:,} credits from citadel safe on planet {planet_id}"
        )

        return {
            "success": True,
            "message": f"Withdrew {amount:,} credits from citadel safe.",
            "credits_withdrawn": amount,
            "safe_balance": safe_current - amount,
            "player_credits": player.credits,
        }

    def _get_defense_buildings(self, planet: Planet) -> Dict[str, int]:
        """Extract defense_buildings sub-dict from planet.active_events JSONB.

        The active_events field stores a dict (or list for legacy data).
        Defense buildings are tracked under the 'defense_buildings' key as
        a mapping of building_type -> count.
        """
        events = planet.active_events
        if isinstance(events, dict):
            return dict(events.get("defense_buildings", {}))
        # Legacy format: active_events may be a list; treat as no buildings
        return {}

    def _set_defense_buildings(self, planet: Planet, buildings: Dict[str, int]) -> None:
        """Persist defense_buildings into the planet.active_events JSONB."""
        events = planet.active_events
        if not isinstance(events, dict):
            # Migrate from legacy list format, preserving old entries
            events = {"legacy_events": events} if events else {}
        # Shallow-copy to ensure SQLAlchemy detects the mutation
        events = dict(events)
        events["defense_buildings"] = buildings
        planet.active_events = events

    def get_available_buildings(self, planet_id: uuid.UUID) -> Dict[str, Any]:
        """Return which defense buildings can be built based on the planet's current citadel level.

        Each entry includes the building spec, current count, max allowed at this level,
        and whether the player can build more.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        current_level = getattr(planet, "citadel_level", 0) or 0
        if current_level < 1:
            return {
                "success": True,
                "message": "No citadel — no buildings available",
                "planet_id": str(planet_id),
                "citadel_level": current_level,
                "buildings": [],
            }

        existing = self._get_defense_buildings(planet)
        buildings: List[Dict[str, Any]] = []

        for building_type, spec in DEFENSE_BUILDINGS.items():
            if current_level < spec["min_citadel_level"]:
                continue

            # Determine max count for the current citadel level
            max_at_level = 0
            for lvl in sorted(spec["max_count"]):
                if current_level >= lvl:
                    max_at_level = spec["max_count"][lvl]
            current_count = existing.get(building_type, 0)

            buildings.append({
                "type": building_type,
                "name": spec["name"],
                "cost": spec["cost"],
                "build_hours": spec["build_hours"],
                "effects": spec["effects"],
                "current_count": current_count,
                "max_count": max_at_level,
                "can_build": current_count < max_at_level,
            })

        return {
            "success": True,
            "message": "Available buildings retrieved",
            "planet_id": str(planet_id),
            "citadel_level": current_level,
            "buildings": buildings,
        }

    def build_defense_building(
        self,
        planet_id: uuid.UUID,
        player_id: uuid.UUID,
        building_type: str,
    ) -> Dict[str, Any]:
        """Construct a defense building on a planet, gated by citadel level and credits.

        Validates the building type, citadel prerequisites, max count, and player funds
        before recording the building and deducting credits.
        """
        # --- Validate building type ---
        if building_type not in DEFENSE_BUILDINGS:
            valid = ", ".join(DEFENSE_BUILDINGS.keys())
            return {
                "success": False,
                "message": f"Unknown building type '{building_type}'. Valid types: {valid}",
            }

        spec = DEFENSE_BUILDINGS[building_type]

        # --- Lock planet to prevent concurrent building races ---
        planet = self.db.query(Planet).filter(Planet.id == planet_id).with_for_update().first()
        if not planet:
            return {"success": False, "message": "Planet not found"}

        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}

        # --- Citadel level check ---
        current_level = getattr(planet, "citadel_level", 0) or 0
        if current_level < spec["min_citadel_level"]:
            return {
                "success": False,
                "message": (
                    f"{spec['name']} requires citadel level {spec['min_citadel_level']}+. "
                    f"Current level: {current_level}."
                ),
            }

        # --- Max count check ---
        max_at_level = 0
        for lvl in sorted(spec["max_count"]):
            if current_level >= lvl:
                max_at_level = spec["max_count"][lvl]

        existing = self._get_defense_buildings(planet)
        current_count = existing.get(building_type, 0)

        if current_count >= max_at_level:
            return {
                "success": False,
                "message": (
                    f"Maximum {spec['name']} capacity reached ({max_at_level}) "
                    f"at citadel level {current_level}."
                ),
            }

        # --- Lock player for credit deduction ---
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return {"success": False, "message": "Player not found"}

        if player.credits < spec["cost"]:
            return {
                "success": False,
                "message": (
                    f"Insufficient credits. Need {spec['cost']:,}, have {player.credits:,}."
                ),
            }

        # --- Execute construction ---
        player.credits -= spec["cost"]

        existing[building_type] = current_count + 1
        self._set_defense_buildings(planet, existing)

        self.db.flush()

        logger.info(
            f"Player {player_id} built {spec['name']} on planet {planet_id} "
            f"(count: {current_count + 1}/{max_at_level})"
        )

        return {
            "success": True,
            "message": (
                f"{spec['name']} construction started! "
                f"Estimated completion: {spec['build_hours']} hours."
            ),
            "building_type": building_type,
            "building_name": spec["name"],
            "count": current_count + 1,
            "max_count": max_at_level,
            "credits_deducted": spec["cost"],
            "player_credits": player.credits,
            "build_hours": spec["build_hours"],
            "effects": spec["effects"],
        }
