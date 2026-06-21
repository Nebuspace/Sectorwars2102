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

from src.core.commodity_economy import get_commodity_credit_values
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
    # CRT WO-K0-3: the two formerly Design-only DEFENSE_BUILDINGS, now cashed into
    # reality. Each is RESEARCH-GATED — placeable through THIS existing flow only
    # once the owning player has unlocked the matching tech node (point-of-use
    # check below in build_defense_building). ``research_node`` names the gate;
    # research_service.player_has_tech reads it.
    #
    # FIX 3 (orchestrator-ruled): magnitudes finalized CONSISTENT with
    # FEATURES/planets/defense.md canon AND the shipped DEFENSE_BUILDINGS scale
    # (scanner 75k/48h/L2 · turret 150k/72h/L3 · orbital 500k/168h/L4). Canon
    # gives EXACT figures for both buildings, so these mirror canon rather than
    # the earlier conservative guesses. Still NO-CANON-PROPOSED (the deploy is
    # HELD until the orchestrator's bless): the only datum canon omits is the RP
    # research-gate cost (a kernel-internal currency canon does not speak to) and
    # the precise combat-effect encoding (the resolver's raw-burst injection is
    # itself 📐 Design-only per defense.md §combat-resolver-integration).
    #
    # rail_gun (defense.md §"Fixed rail gun batteries"): citadel L4+; 4@L4 /
    # 10@L5; 150,000 cr; 72h; PER-SHIP-SIZE-MULTIPLIER anti-capital weapon (base
    # damage 1,000–3,000 before the ship-class multiplier table). ``effects``
    # encodes the canon role: a base raw burst plus the per-ship-class multiplier
    # table, so the eventual resolver wiring reads the canon numbers straight off
    # the catalog without a reshape.
    "rail_gun": {
        "name": "Rail Gun Battery",
        "min_citadel_level": 4,
        "max_count": {4: 4, 5: 10},
        "cost": 150000,
        "build_hours": 72,
        "effects": {
            # Per-ship-size-multiplier weapon (defense.md §rail-gun damage table).
            "weapon_kind": "anti_capital",
            "base_damage_min": 1000,
            "base_damage_max": 3000,
            "ship_size_multiplier_pct": {
                "CARRIER": 200, "COLONY_SHIP": 200, "CARGO_HAULER": 150,
                "DEFENDER": 120, "LIGHT_FREIGHTER": 50, "WARP_JUMPER": 25,
                "FAST_COURIER": 15, "SCOUT_SHIP": 10,
            },
        },
        "research_node": "t.defense.railgun.1",
    },
    # planetary_defense_grid (defense.md §"Defense grid"): citadel L3+; 200,000 cr
    # + 15,000 equipment; 96h; a DRONE-DAMAGE MODIFIER (+15% drone damage &
    # accuracy, upgradable to L2 for +25% total). Key renamed defense_grid ->
    # planetary_defense_grid (blessed rename) to avoid colliding with the
    # unrelated Station.defense_grid boolean. ``effects`` encodes the canon
    # drone-damage role; max_count 1 per level is the L1 install (the +25% L2
    # upgrade is a later upgrade path, not a second building).
    "planetary_defense_grid": {
        "name": "Planetary Defense Grid",
        "min_citadel_level": 3,
        "max_count": {3: 1, 4: 1, 5: 1},
        "cost": 200000,
        "build_hours": 96,
        "effects": {
            # Drone-damage modifier (defense.md §defense-grid).
            "drone_damage_bonus_pct": 15,
            "drone_accuracy_bonus_pct": 15,
        },
        "research_node": "t.defense.grid.1",
    },
    # planet_minefield (WO-G7): citadel L3+; per-level capacity 1@L3 / 2@L4 /
    # 3@L5; 100,000 cr + 10,000 equipment; 48h. A field seeds 20 proximity mines.
    # MIRRORS rail_gun EXACTLY: same key shape (name / min_citadel_level /
    # max_count / cost / build_hours / effects), consumed by the UNCHANGED
    # data-driven build_defense_building flow (credit charge, slot reserve, 48h
    # settle timer, per-citadel-level capacity gate, below-gate rejection). As
    # rail_gun stores its DEFERRED per-shot combat data inside ``effects`` (no
    # resolver wiring yet), planet_minefield stores its DEFERRED per-mine combat
    # data inside ``effects`` the same way — the per-mine damage INJECTION is
    # deferred (no combat_service change here), exactly like rail_gun. The
    # 10,000-equipment requirement and the 20-mines-per-field datum are recorded
    # in ``effects`` (rail_gun's metadata home) rather than as new top-level
    # keys, so the catalog shape stays identical to rail_gun and the existing
    # credit-only build flow needs no change.
    "planet_minefield": {
        "name": "Planetary Minefield",
        "min_citadel_level": 3,
        "max_count": {3: 1, 4: 2, 5: 3},
        "cost": 100000,
        "build_hours": 48,
        "effects": {
            # Deferred per-mine combat data (no resolver wiring yet — mirrors
            # rail_gun's deferred per-shot data living here).
            "weapon_kind": "proximity_mine",
            "mines_per_field": 20,
            # Equipment material requirement recorded as catalog metadata
            # (build_defense_building charges credits only, like rail_gun).
            "equipment_cost": 10000,
        },
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

# Credit-equivalent valuation for commodities stored in the citadel safe.
# Canon (citadels.md "Safe") caps the safe by a single cr-equivalent total; the
# safe_storage figures above are that cap. These per-unit values (the economy's
# base trade prices — see ADR-0082) convert stored commodities into that
# cr-equivalent so credits and goods share one capacity pool.
#
# WO-Y / ADR-0082: these values now derive from the SINGLE source of truth in
# src.core.commodity_economy (which also feeds the trading-engine price ranges),
# so the safe and the market can no longer silently disagree on base prices.
# Keys keep the citadel/planet "fuel_ore" vocabulary (the planet.fuel_ore Column
# and the citadel API ^(fuel_ore|organics|equipment)$ contract); the single
# table speaks canonical "ore" and is remapped here at the domain boundary.
# Behaviour-preserving: reproduces fuel_ore 15 / organics 18 / equipment 35
# exactly (guarded by import-time assertions in commodity_economy).
COMMODITY_CREDIT_VALUE = get_commodity_credit_values()


def citadel_passive_defense(level: int) -> int:
    """Passive defensive contribution of a citadel at the given level.

    The citadel's drone garrison (CITADEL_LEVELS[level]["drone_capacity"]) IS its
    passive defense — a fortified citadel holds more defensive drones (combat_service
    reads the same garrison for the defense-drones passive). This reuses the single
    CITADEL_LEVELS mapping rather than introducing a divergent level->defense table.
    Clamps to the known range; an unknown level degrades to 0 (never raises).
    """
    info = CITADEL_LEVELS.get(int(level or 0))
    if not info:
        return 0
    return int(info.get("drone_capacity", 0) or 0)


def citadel_passive_defense_rating(planet) -> int:
    """Defense rating contributed by a planet's citadel, including the partial
    bonus earned WHILE an upgrade is in progress (WO-G6).

    - Idle (no upgrade running): the full passive value of the current level.
    - Upgrade in progress (``citadel_upgrading`` set, not yet completed): the
      current level's full value PLUS 50% of the NEXT level's passive-defense delta,
      i.e. 0.5 x (defense(next_level) - defense(current_level)). The remaining 50%
      lands automatically on completion when ``citadel_level`` increments to the
      next level (so there is no double-count).
    - Defensive throughout: a missing field (e.g. a planet model without the
      citadel columns) degrades to the bare current-level value or 0, never raises.
    """
    current_level = int(getattr(planet, "citadel_level", 0) or 0)
    base = citadel_passive_defense(current_level)

    # Partial in-progress bonus only when an upgrade is actively running and a
    # higher level exists to upgrade toward.
    upgrading = bool(getattr(planet, "citadel_upgrading", False))
    if not upgrading or current_level >= 5:
        return base

    next_level = current_level + 1
    delta = citadel_passive_defense(next_level) - base
    if delta <= 0:
        return base
    return base + int(0.5 * delta)


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
            # Commodity safe holdings + the shared cr-equivalent accounting.
            "safe_commodities": self._get_safe_commodities(planet),
            "safe_total_value": self._safe_total_value(planet),
            "commodity_values": COMMODITY_CREDIT_VALUE,
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

        # Population gate: the current level must be filled before advancing
        # (citadels.md upgrade workflow step 1 — "current pop must hit max for
        # current level"). Prevents rushing straight to L5 on a near-empty colony.
        current_info = CITADEL_LEVELS[current_level]
        required_pop = current_info.get("max_population", 0) or 0
        if (planet.colonists or 0) < required_pop:
            return {
                "success": False,
                "message": (
                    f"Population must reach {required_pop:,} ({current_info['name']} capacity) "
                    f"before upgrading. Current colonists: {(planet.colonists or 0):,}."
                ),
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

    def cancel_upgrade(self, planet_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Cancel an in-progress citadel upgrade. Player-initiated cancel refunds
        50% of the credits paid (resources are not returned — they covered
        irreversible setup work), per citadels.md / ADR-0059 N-F3."""
        planet = self.db.query(Planet).filter(Planet.id == planet_id).with_for_update().first()
        if not planet:
            return {"success": False, "message": "Planet not found"}
        if planet.owner_id != player_id:
            return {"success": False, "message": "You do not own this planet"}
        if not getattr(planet, "citadel_upgrading", False):
            return {"success": False, "message": "No citadel upgrade is in progress"}

        current_level = getattr(planet, "citadel_level", 0) or 0
        target_level = current_level + 1
        target_info = CITADEL_LEVELS.get(target_level, {})
        refund = int((target_info.get("upgrade_cost", 0) or 0) * 0.5)

        if refund > 0:
            player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
            if player:
                player.credits += refund

        planet.citadel_upgrading = False
        planet.citadel_upgrade_started_at = None
        planet.citadel_upgrade_complete_at = None
        self.db.flush()

        logger.info(
            f"Planet {planet_id} citadel upgrade to level {target_level} cancelled "
            f"by player {player_id}; refunded {refund} credits (50%)"
        )
        return {
            "success": True,
            "message": (
                f"Upgrade to {target_info.get('name', f'level {target_level}')} cancelled — "
                f"{refund:,} cr (50%) refunded. Resources spent are not returned."
            ),
            "credits_refunded": refund,
            "citadel_level": current_level,
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

    # --- Commodity safe storage -------------------------------------------------

    def _get_safe_commodities(self, planet: Planet) -> Dict[str, int]:
        """Extract the safe's commodity holdings from planet.active_events JSONB."""
        events = planet.active_events
        if isinstance(events, dict):
            sc = events.get("safe_commodities", {})
            if isinstance(sc, dict):
                return {k: int(v) for k, v in sc.items()}
        return {}

    def _set_safe_commodities(self, planet: Planet, commodities: Dict[str, int]) -> None:
        """Persist the safe's commodity holdings into planet.active_events JSONB."""
        events = planet.active_events
        if not isinstance(events, dict):
            events = {"legacy_events": events} if events else {}
        events = dict(events)
        # Drop zero entries to keep the JSONB tidy.
        events["safe_commodities"] = {k: v for k, v in commodities.items() if v > 0}
        planet.active_events = events

    def _safe_total_value(self, planet: Planet) -> int:
        """Credit-equivalent value of everything in the safe (credits + commodities)."""
        total = int(getattr(planet, "citadel_safe_credits", 0) or 0)
        for commodity, qty in self._get_safe_commodities(planet).items():
            total += int(qty) * COMMODITY_CREDIT_VALUE.get(commodity, 0)
        return total

    def deposit_commodity_to_safe(
        self, planet_id: uuid.UUID, player_id: uuid.UUID, commodity: str, amount: int
    ) -> Dict[str, Any]:
        """Move a commodity from the planet stockpile into the protected citadel safe.

        Goods in the safe are protected from raiders (citadels.md "Safe"). The
        safe is bounded by a single cr-equivalent capacity shared with stored
        credits; each commodity unit counts toward it at COMMODITY_CREDIT_VALUE.
        """
        if commodity not in COMMODITY_CREDIT_VALUE:
            valid = ", ".join(COMMODITY_CREDIT_VALUE.keys())
            return {"success": False, "message": f"Unknown commodity '{commodity}'. Valid: {valid}"}
        if amount <= 0:
            return {"success": False, "message": "Deposit amount must be positive"}

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
        if (getattr(planet, "citadel_level", 0) or 0) < 1:
            return {"success": False, "message": "Planet does not have a citadel"}

        on_hand = int(getattr(planet, commodity, 0) or 0)
        if on_hand < amount:
            return {
                "success": False,
                "message": f"Not enough {commodity.replace('_', ' ')} on the planet. Have {on_hand:,}, need {amount:,}.",
            }

        capacity = CITADEL_LEVELS[planet.citadel_level]["safe_storage"]
        unit_value = COMMODITY_CREDIT_VALUE[commodity]
        added_value = amount * unit_value
        if self._safe_total_value(planet) + added_value > capacity:
            room = max(0, capacity - self._safe_total_value(planet))
            return {
                "success": False,
                "message": (
                    f"Safe capacity is {capacity:,} cr-equivalent. "
                    f"Room for {room // unit_value:,} more {commodity.replace('_', ' ')}."
                ),
            }

        commodities = self._get_safe_commodities(planet)
        commodities[commodity] = commodities.get(commodity, 0) + amount
        setattr(planet, commodity, on_hand - amount)
        self._set_safe_commodities(planet, commodities)
        self.db.flush()

        logger.info(
            f"Player {player_id} deposited {amount:,} {commodity} into citadel safe on planet {planet_id}"
        )
        return {
            "success": True,
            "message": f"Stored {amount:,} {commodity.replace('_', ' ')} in the citadel safe.",
            "commodity": commodity,
            "amount_deposited": amount,
            "safe_commodities": commodities,
            "planet_stockpile": on_hand - amount,
            "safe_total_value": self._safe_total_value(planet),
            "safe_capacity": capacity,
        }

    def withdraw_commodity_from_safe(
        self, planet_id: uuid.UUID, player_id: uuid.UUID, commodity: str, amount: int
    ) -> Dict[str, Any]:
        """Move a commodity from the citadel safe back onto the planet stockpile."""
        if commodity not in COMMODITY_CREDIT_VALUE:
            valid = ", ".join(COMMODITY_CREDIT_VALUE.keys())
            return {"success": False, "message": f"Unknown commodity '{commodity}'. Valid: {valid}"}
        if amount <= 0:
            return {"success": False, "message": "Withdrawal amount must be positive"}

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
        if (getattr(planet, "citadel_level", 0) or 0) < 1:
            return {"success": False, "message": "Planet does not have a citadel"}

        commodities = self._get_safe_commodities(planet)
        in_safe = int(commodities.get(commodity, 0))
        if in_safe < amount:
            return {
                "success": False,
                "message": f"Not enough {commodity.replace('_', ' ')} in the safe. Have {in_safe:,}, requested {amount:,}.",
            }

        commodities[commodity] = in_safe - amount
        setattr(planet, commodity, int(getattr(planet, commodity, 0) or 0) + amount)
        self._set_safe_commodities(planet, commodities)
        self.db.flush()

        logger.info(
            f"Player {player_id} withdrew {amount:,} {commodity} from citadel safe on planet {planet_id}"
        )
        return {
            "success": True,
            "message": f"Withdrew {amount:,} {commodity.replace('_', ' ')} from the citadel safe.",
            "commodity": commodity,
            "amount_withdrawn": amount,
            "safe_commodities": {k: v for k, v in commodities.items() if v > 0},
            "planet_stockpile": int(getattr(planet, commodity, 0) or 0),
            "safe_total_value": self._safe_total_value(planet),
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

    def _get_build_queue(self, planet: Planet) -> List[Dict[str, Any]]:
        """Extract the in-progress defense-building construction queue from active_events.

        Each queue entry is {type, started_at(iso), complete_at(iso)} — a building under
        construction that has NOT yet joined the operational defense_buildings counts.
        """
        events = planet.active_events
        if isinstance(events, dict):
            queue = events.get("defense_build_queue", [])
            return [dict(e) for e in queue] if isinstance(queue, list) else []
        return []

    def _set_build_queue(self, planet: Planet, queue: List[Dict[str, Any]]) -> None:
        """Persist the defense-building construction queue into active_events JSONB."""
        events = planet.active_events
        if not isinstance(events, dict):
            events = {"legacy_events": events} if events else {}
        events = dict(events)
        events["defense_build_queue"] = queue
        planet.active_events = events

    def _settle_build_queue(self, planet: Planet, now: datetime) -> bool:
        """Complete any defense buildings whose construction timer has elapsed.

        Lazy advance-on-read (mirrors citadel check_upgrade_completion): finished queue
        entries move into the operational defense_buildings counts. Returns True if
        anything changed, so callers can persist.
        """
        events = planet.active_events
        if not isinstance(events, dict):
            return False
        queue = events.get("defense_build_queue", [])
        if not isinstance(queue, list) or not queue:
            return False
        buildings = dict(events.get("defense_buildings", {}))
        remaining: List[Dict[str, Any]] = []
        changed = False
        for entry in queue:
            complete_at = entry.get("complete_at")
            done = False
            if complete_at:
                try:
                    done = now >= datetime.fromisoformat(complete_at)
                except (ValueError, TypeError):
                    done = True  # malformed timestamp: settle rather than strand the build
            if done:
                btype = entry.get("type")
                if btype:
                    buildings[btype] = buildings.get(btype, 0) + 1
                changed = True
            else:
                remaining.append(entry)
        if not changed:
            return False
        new_events = dict(events)
        new_events["defense_buildings"] = buildings
        new_events["defense_build_queue"] = remaining
        planet.active_events = new_events
        self.db.flush()
        return True

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

        # Lazy advance-on-read: complete any builds whose timer elapsed, then persist.
        now = datetime.now(UTC)
        if self._settle_build_queue(planet, now):
            self.db.commit()

        existing = self._get_defense_buildings(planet)
        queue = self._get_build_queue(planet)
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

            # In-progress builds of this type, soonest completion first
            in_progress: List[Dict[str, Any]] = []
            for entry in queue:
                if entry.get("type") != building_type:
                    continue
                complete_at = entry.get("complete_at")
                remaining_seconds = 0
                if complete_at:
                    try:
                        remaining_seconds = max(
                            0, int((datetime.fromisoformat(complete_at) - now).total_seconds())
                        )
                    except (ValueError, TypeError):
                        remaining_seconds = 0
                in_progress.append({
                    "complete_at": complete_at,
                    "remaining_seconds": remaining_seconds,
                })
            in_progress.sort(key=lambda e: e["remaining_seconds"])
            queued_count = len(in_progress)

            buildings.append({
                "type": building_type,
                "name": spec["name"],
                "cost": spec["cost"],
                "build_hours": spec["build_hours"],
                "effects": spec["effects"],
                "current_count": current_count,
                "queued_count": queued_count,
                "in_progress": in_progress,
                "max_count": max_at_level,
                # A pending build reserves a slot, so capacity counts operational + queued.
                "can_build": (current_count + queued_count) < max_at_level,
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

        # Lazy advance-on-read: complete any finished builds before re-checking capacity.
        now = datetime.now(UTC)
        self._settle_build_queue(planet, now)

        # --- Research gate (CRT WO-K0-3) ---
        # A building type carrying a ``research_node`` is placeable through this
        # existing flow ONLY if the owning player has unlocked that node. This is
        # the point-of-use read (research is a leaf — citadel calls into research,
        # never the reverse). No new placement path; one guard inserted into the
        # existing one. The player is the owner (ownership checked above), read
        # here without a lock (a pure ledger read); the credit-deduction lock is
        # still acquired below.
        gate_node = spec.get("research_node")
        if gate_node:
            from src.services import research_service
            gate_player = self.db.query(Player).filter(Player.id == player_id).first()
            if gate_player is None or not research_service.player_has_tech(gate_player, gate_node):
                node = research_service.tech_tree.get_node(gate_node)
                node_name = node["name"] if node else gate_node
                return {
                    "success": False,
                    "message": (
                        f"{spec['name']} requires the '{node_name}' research to be "
                        f"unlocked first."
                    ),
                }

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

        # --- Max count check (operational + in-progress reserve the slots) ---
        max_at_level = 0
        for lvl in sorted(spec["max_count"]):
            if current_level >= lvl:
                max_at_level = spec["max_count"][lvl]

        existing = self._get_defense_buildings(planet)
        current_count = existing.get(building_type, 0)
        queue = self._get_build_queue(planet)
        queued_count = sum(1 for q in queue if q.get("type") == building_type)

        if current_count + queued_count >= max_at_level:
            in_progress_note = f" ({queued_count} already under construction)" if queued_count else ""
            return {
                "success": False,
                "message": (
                    f"Maximum {spec['name']} capacity reached ({max_at_level}) "
                    f"at citadel level {current_level}{in_progress_note}."
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

        # --- Execute construction: deduct credits and enqueue a timed build ---
        player.credits -= spec["cost"]

        complete_at = now + timedelta(hours=spec["build_hours"])
        queue.append({
            "type": building_type,
            "started_at": now.isoformat(),
            "complete_at": complete_at.isoformat(),
        })
        self._set_build_queue(planet, queue)

        self.db.flush()

        logger.info(
            f"Player {player_id} started building {spec['name']} on planet {planet_id} "
            f"(completes {complete_at.isoformat()}, "
            f"operational: {current_count}/{max_at_level}, queued: {queued_count + 1})"
        )

        return {
            "success": True,
            "complete_at": complete_at.isoformat(),
            "remaining_seconds": int(spec["build_hours"] * 3600),
            "queued_count": queued_count + 1,
            "message": (
                f"{spec['name']} construction started! "
                f"Estimated completion: {spec['build_hours']} hours."
            ),
            "building_type": building_type,
            "building_name": spec["name"],
            "count": current_count,
            "max_count": max_at_level,
            "credits_deducted": spec["cost"],
            "player_credits": player.credits,
            "build_hours": spec["build_hours"],
            "effects": spec["effects"],
        }
