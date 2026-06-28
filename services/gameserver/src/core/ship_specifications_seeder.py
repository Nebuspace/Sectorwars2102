"""
Ship Specifications Seeder
Seeds the database with ship specification data based on SHIP_TYPES.md documentation
"""

from sqlalchemy.orm import Session
from src.models.ship import ShipSpecification, ShipType, ShipSize
import logging

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# [NO-CANON] Per-hull combat-mitigation table (B3) — shield_resistance &
# armor_rating. These two ShipSpecification columns (models/ship.py:222-223,
# defaulted 0.0) are CONSUMED by the combat resolver
# (combat_service._apply_weapon_damage, combat_service.py:2277-2287): they are
# FRACTIONS of incoming damage absorbed (shield component & hull component
# respectively), clamped by _resistance_fraction to [0.0, 0.9]. Until now the
# seeder never set them, so every hull absorbed 0% — these mitigations were
# inert. This table makes them non-zero.
#
# *** THESE ARE [NO-CANON] MAGNITUDES — sw2102-docs gives no shield_resistance /
# armor_rating numbers. The values below are a PROPOSAL for Max to bless. ***
# Design: conservative + gently hull/class-tiered. Civilian / utility hulls get
# little-to-nothing; dedicated combat hulls get the most. Even the heaviest
# combat hull stays at 0.20 (well under the 0.90 clamp) so this is a SMALL
# rebalance, not a wall of immunity.
#
# *** BALANCE NOTE: this CHANGES combat absorption from 0% -> non-zero. It is a
# deliberate (conservative) balance change; flagged for Max. ***
#
# WIRING CAVEAT (out of THIS file's lane, flag for a follow-up WO): combat reads
# these off the Ship ROW (getattr(defender_ship, "shield_resistance"...)), and
# the three Ship() constructors (ship_service.create_ship:63-112,
# npc_spawn_service:391, first_login_service:1642) do NOT yet copy
# shield_resistance / armor_rating from the spec, so live Ship rows stay at the
# column default 0.0. Seeding the SPEC is necessary but not sufficient — a
# downstream task must copy spec.shield_resistance / spec.armor_rating onto new
# Ship rows for these to take effect in combat.
# ----------------------------------------------------------------------------
_NO_CANON_MITIGATION = {
    # ESCAPE_POD: indestructible already; no mitigation needed. Keep 0.0.
    ShipType.ESCAPE_POD:      {"shield_resistance": 0.0,  "armor_rating": 0.0},
    # Civilian / light haulers & couriers — token armor, ~no shield resistance.
    ShipType.FAST_COURIER:    {"shield_resistance": 0.0,  "armor_rating": 0.02},
    ShipType.CITIZEN_CLIPPER: {"shield_resistance": 0.0,  "armor_rating": 0.02},  # mirrors FAST_COURIER (P2W firewall)
    ShipType.SCOUT_SHIP:      {"shield_resistance": 0.0,  "armor_rating": 0.02},
    ShipType.LIGHT_FREIGHTER: {"shield_resistance": 0.02, "armor_rating": 0.03},
    ShipType.CARGO_HAULER:    {"shield_resistance": 0.03, "armor_rating": 0.05},
    ShipType.COLONY_SHIP:     {"shield_resistance": 0.03, "armor_rating": 0.05},
    ShipType.WARP_JUMPER:     {"shield_resistance": 0.05, "armor_rating": 0.05},
    # Dedicated combat hulls — meaningful but still well under the 0.90 clamp.
    ShipType.DEFENDER:        {"shield_resistance": 0.10, "armor_rating": 0.10},
    ShipType.CARRIER:         {"shield_resistance": 0.15, "armor_rating": 0.15},
    # NPC-only Interdictor pursuit hulls — toughest, but conservative (<= 0.20).
    ShipType.NPC_MARSHAL_INTERDICTOR:  {"shield_resistance": 0.15, "armor_rating": 0.15},
    ShipType.NPC_SENTINEL_INTERDICTOR: {"shield_resistance": 0.20, "armor_rating": 0.20},
}

# Ship specifications based on DOCS/FEATURES/SHIP_TYPES.md
SHIP_SPECIFICATIONS = {
    ShipType.ESCAPE_POD: {
        "ship_size": ShipSize.TINY,  # canon ships.md:324
        "base_cost": 0,
        "speed": 0.25,
        "turn_cost": 10,  # Very high turn cost - escape pods are not meant for travel
        "attack_turn_cost": 10000,  # Effectively prevents combat from escape pods
        "max_cargo": 50,
        "max_colonists": 1,
        "max_drones": 0,
        "max_shields": 150,
        "shield_recharge_rate": 5.0,
        "hull_points": 200,
        "evasion": 10,
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": False,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 1,
        "attack_rating": 0,
        "defense_rating": 5,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 0,
        "fuel_efficiency": 50,
        "max_upgrade_levels": {
            "ENGINE": 2,
            "CARGO_HOLD": 1,
            "SHIELD": 3,
            "HULL": 3,
            "SENSOR": 2,
            "DRONE_BAY": 0,
            "GENESIS_CONTAINMENT": 0,
            "MAINTENANCE_SYSTEM": 1
        },
        "special_abilities": ["indestructible", "automatic_ejection", "emergency_beacon"],
        "description": "Basic survival craft that cannot be destroyed. When a player's ship is destroyed, they are automatically ejected into their escape pod.",
        "acquisition_methods": ["automatic", "emergency_ejection"],
        "faction_requirements": None
    },
    ShipType.LIGHT_FREIGHTER: {
        "ship_size": ShipSize.MEDIUM,  # canon ships.md:326
        "base_cost": 80000,
        "speed": 1.0,
        "turn_cost": 1,
        "attack_turn_cost": 12,  # Light combat vessel
        "max_cargo": 500,
        "max_colonists": 5,
        "max_drones": 2,
        "max_shields": 300,
        "shield_recharge_rate": 10.0,
        "hull_points": 400,
        "evasion": 15,
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 2,
        "attack_rating": 15,
        "defense_rating": 20,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 1,
        "fuel_efficiency": 80,
        "max_upgrade_levels": {
            "ENGINE": 3,
            "CARGO_HOLD": 3,
            "SHIELD": 3,
            "HULL": 3,
            "SENSOR": 3,
            "DRONE_BAY": 2,
            "GENESIS_CONTAINMENT": 0,
            "MAINTENANCE_SYSTEM": 3
        },
        "special_abilities": ["versatile_configuration"],
        "description": "A common vessel throughout the galaxy, offering balanced performance across all areas. The Light Freighter serves as the standard starting ship for many pilots.",
        "acquisition_methods": ["purchase", "salvage", "reputation_reward"],
        "faction_requirements": None
    },
    ShipType.CARGO_HAULER: {
        "ship_size": ShipSize.LARGE,  # canon ships.md:327
        "base_cost": 250000,
        "speed": 0.5,
        "turn_cost": 2,
        "attack_turn_cost": 20,  # Slow, heavy hauler
        "max_cargo": 1000,
        "max_colonists": 10,
        "max_drones": 2,
        "max_shields": 400,
        "shield_recharge_rate": 8.0,
        "hull_points": 600,
        "evasion": 5,
        "genesis_compatible": True,
        "max_genesis_devices": 2,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 2,
        "attack_rating": 10,
        "defense_rating": 30,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 3,
        "fuel_efficiency": 60,
        "max_upgrade_levels": {
            "ENGINE": 2,
            "CARGO_HOLD": 4,
            "SHIELD": 3,
            "HULL": 4,
            "SENSOR": 2,
            "DRONE_BAY": 2,
            "GENESIS_CONTAINMENT": 2,
            "MAINTENANCE_SYSTEM": 3
        },
        "special_abilities": ["cargo_compression"],
        "description": "The backbone of interstellar commerce. These reliable vessels sacrifice speed for substantial cargo capacity, making them ideal for bulk commodity transport.",
        "acquisition_methods": ["purchase", "salvage"],
        "faction_requirements": None
    },
    ShipType.FAST_COURIER: {
        "ship_size": ShipSize.SMALL,  # canon ships.md:325
        "base_cost": 50000,
        "speed": 2.0,
        "turn_cost": 1,
        "attack_turn_cost": 8,  # Fast but light combat
        "max_cargo": 200,
        "max_colonists": 2,
        "max_drones": 0,
        "max_shields": 200,
        "shield_recharge_rate": 15.0,
        "hull_points": 300,
        "evasion": 35,
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 3,
        "attack_rating": 5,
        "defense_rating": 10,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 2,
        "fuel_efficiency": 90,
        "max_upgrade_levels": {
            "ENGINE": 4,
            "CARGO_HOLD": 2,
            "SHIELD": 2,
            "HULL": 2,
            "SENSOR": 4,
            "DRONE_BAY": 0,
            "GENESIS_CONTAINMENT": 0,
            "MAINTENANCE_SYSTEM": 2
        },
        "special_abilities": ["stealth_systems"],
        "description": "When speed matters more than capacity, the Fast Courier excels. Their advanced engines and lightweight design allow rapid transit between sectors.",
        "acquisition_methods": ["purchase", "reputation_reward"],
        "faction_requirements": None
    },
    # ------------------------------------------------------------------
    # Galactic-Citizen courier (GC-C). P2W FIREWALL: anchored EXACTLY to
    # FAST_COURIER above — every combat/income axis (attack_rating 5,
    # defense_rating 10, max_shields 200, hull_points 300, max_genesis_devices 0,
    # speed 2.0, max_cargo 200, NO income field) is mirrored, NEVER exceeded. The
    # only citizen differences are SHAPE/utility-breadth/QoL/cosmetic (one extra
    # maintenance-locked super slot, see _SHIP_MODS_LAYOUT) plus acquisition gating.
    # Overrides vs FAST_COURIER: acquisition_methods (membership unlocks the buy,
    # credits still pay), faction_requirements (None — gated by citizenship, not
    # faction), and a Citizen-flavored description. is_npc_only defaults False
    # (player-facing → appears in /catalog).
    # ------------------------------------------------------------------
    ShipType.CITIZEN_CLIPPER: {
        "ship_size": ShipSize.SMALL,  # canon ships.md:325 — same as Fast Courier anchor
        "base_cost": 50000,
        "speed": 2.0,
        "turn_cost": 1,
        "attack_turn_cost": 8,  # mirrors Fast Courier — combat axis not exceeded
        "max_cargo": 200,
        "max_colonists": 2,
        "max_drones": 0,
        "max_shields": 200,
        "shield_recharge_rate": 15.0,
        "hull_points": 300,
        "evasion": 35,
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 3,
        "attack_rating": 5,
        "defense_rating": 10,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 2,
        "fuel_efficiency": 90,
        "max_upgrade_levels": {
            "ENGINE": 4,
            "CARGO_HOLD": 2,
            "SHIELD": 2,
            "HULL": 2,
            "SENSOR": 4,
            "DRONE_BAY": 0,
            "GENESIS_CONTAINMENT": 0,
            "MAINTENANCE_SYSTEM": 2
        },
        "special_abilities": ["stealth_systems"],
        "description": "Citizen Clipper — a Galactic-Citizen courier. A membership-issue re-skin of the Fast Courier: identical speed, handling, and survivability, distinguished only by Citizen styling and a dedicated maintenance bay. No edge in combat or income — a badge of citizenship, not a power spike.",
        # Membership unlocks the BUY; credits (base_cost) still pay. NOT "purchase"
        # (open market) nor "reputation_reward" (faction grind) — "citizen" gates on
        # Galactic-Citizen membership at the catalog/purchase layer.
        "acquisition_methods": ["citizen"],
        "faction_requirements": None
    },
    ShipType.SCOUT_SHIP: {
        "ship_size": ShipSize.SMALL,  # canon ships.md:325
        "base_cost": 30000,
        "speed": 2.5,
        "turn_cost": 1,
        "attack_turn_cost": 5,  # Fast strike capability
        "max_cargo": 100,
        "max_colonists": 1,
        "max_drones": 1,
        "max_shields": 150,
        "shield_recharge_rate": 12.0,
        "hull_points": 200,
        "evasion": 45,
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 5,
        "attack_rating": 8,
        "defense_rating": 8,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 2,
        "fuel_efficiency": 95,
        "max_upgrade_levels": {
            "ENGINE": 4,
            "CARGO_HOLD": 1,
            "SHIELD": 2,
            "HULL": 2,
            "SENSOR": 5,
            "DRONE_BAY": 1,
            "GENESIS_CONTAINMENT": 0,
            "MAINTENANCE_SYSTEM": 2
        },
        "special_abilities": ["advanced_sensors"],
        "description": "Lightweight and agile, Scout Ships excel at charting new territories and identifying potential colonization targets.",
        "acquisition_methods": ["purchase", "reputation_reward"],
        "faction_requirements": None
    },
    ShipType.COLONY_SHIP: {
        "ship_size": ShipSize.LARGE,  # canon ships.md:327
        "base_cost": 500000,
        "speed": 0.4,
        "turn_cost": 3,
        "attack_turn_cost": 35,  # Not meant for combat
        "max_cargo": 1000,
        "max_colonists": 50,
        "max_drones": 2,
        "max_shields": 400,
        "shield_recharge_rate": 6.0,
        "hull_points": 600,
        "evasion": 0,
        "genesis_compatible": True,
        "max_genesis_devices": 5,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 2,
        "attack_rating": 5,
        "defense_rating": 25,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 7,
        "fuel_efficiency": 40,
        "max_upgrade_levels": {
            "ENGINE": 1,
            "CARGO_HOLD": 3,
            "SHIELD": 3,
            "HULL": 4,
            "SENSOR": 3,
            "DRONE_BAY": 2,
            "GENESIS_CONTAINMENT": 4,
            "MAINTENANCE_SYSTEM": 4
        },
        "special_abilities": ["terraforming_modules"],
        "description": "Designed specifically for establishing new outposts on distant worlds. Equipped with terraforming technology and space for colonists, these ships are essential for expansion.",
        "acquisition_methods": ["purchase"],
        "faction_requirements": None
    },
    ShipType.DEFENDER: {
        "ship_size": ShipSize.MEDIUM,  # canon ships.md:326
        "base_cost": 300000,
        "speed": 1.0,
        "turn_cost": 1,
        "attack_turn_cost": 18,  # Primary combat vessel
        "max_cargo": 400,
        "max_colonists": 8,
        "max_drones": 6,
        "max_shields": 700,
        "shield_recharge_rate": 20.0,
        "hull_points": 800,
        "evasion": 20,
        "genesis_compatible": True,
        "max_genesis_devices": 3,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 3,
        "attack_rating": 40,
        "defense_rating": 45,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 5,
        "fuel_efficiency": 70,
        "max_upgrade_levels": {
            "ENGINE": 3,
            "CARGO_HOLD": 2,
            "SHIELD": 4,
            "HULL": 4,
            "SENSOR": 3,
            "DRONE_BAY": 3,
            "GENESIS_CONTAINMENT": 2,
            "MAINTENANCE_SYSTEM": 3
        },
        "special_abilities": ["shield_projection"],
        "description": "The primary combat vessel, featuring reinforced shields and multiple drone bays. Defenders form the core of any security fleet, balancing offensive capabilities with reasonable cargo space.",
        "acquisition_methods": ["purchase", "reputation_reward"],
        "faction_requirements": None
    },
    ShipType.CARRIER: {
        "ship_size": ShipSize.CAPITAL,  # canon ships.md:328 — only capital hull; not-dockable / not-towable
        "base_cost": 1500000,
        "speed": 0.75,
        "turn_cost": 2,
        "attack_turn_cost": 45,  # Massive fleet combat vessel
        "max_cargo": 800,
        "max_colonists": 20,
        "max_drones": 12,
        "max_shields": 800,
        "shield_recharge_rate": 15.0,
        "hull_points": 900,
        "evasion": 5,
        "genesis_compatible": True,
        "max_genesis_devices": 5,
        "warp_compatible": True,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 4,
        "attack_rating": 50,
        "defense_rating": 60,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 10,
        "fuel_efficiency": 50,
        "max_upgrade_levels": {
            "ENGINE": 2,
            "CARGO_HOLD": 3,
            "SHIELD": 4,
            "HULL": 5,
            "SENSOR": 3,
            "DRONE_BAY": 4,
            "GENESIS_CONTAINMENT": 3,
            "MAINTENANCE_SYSTEM": 4
        },
        "special_abilities": ["fleet_coordination"],
        "description": "These massive vessels serve as mobile headquarters for large operations, housing multiple drone squadrons and providing logistical support across vast distances.",
        "acquisition_methods": ["purchase"],
        # Canon ship-roster.md:51 — Carrier requires terran_federation ≥ TRUSTED
        "faction_requirements": {"terran_federation": "TRUSTED"}
    },
    ShipType.WARP_JUMPER: {
        "ship_size": ShipSize.LARGE,  # canon ships.md:327
        "base_cost": 1000000,
        "speed": 0.0,  # Uses quantum jump instead
        "turn_cost": 1,
        "attack_turn_cost": 100,  # Specialized vessel, poor combat
        "max_cargo": 200,
        "max_colonists": 3,
        "max_drones": 0,
        "max_shields": 500,
        "shield_recharge_rate": 25.0,
        "hull_points": 600,
        "evasion": 30,
        "genesis_compatible": True,
        "max_genesis_devices": 1,
        "warp_compatible": True,
        "warp_creation_capable": True,
        "quantum_jump_capable": True,
        "scanner_range": 8,
        "attack_rating": 10,
        "defense_rating": 35,
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        "construction_time": 21,  # 3-5 days
        "fuel_efficiency": 20,
        "max_upgrade_levels": {
            "ENGINE": 0,  # Cannot upgrade engines
            "CARGO_HOLD": 1,
            "SHIELD": 3,
            "HULL": 3,
            "SENSOR": 5,
            "DRONE_BAY": 0,
            "GENESIS_CONTAINMENT": 1,
            "MAINTENANCE_SYSTEM": 5
        },
        "special_abilities": ["quantum_jump", "warp_gate_creation"],
        "description": "A highly specialized vessel containing modified warp gate technology that allows it to make directed quantum jumps across multiple sectors. The Warp Jumper is specifically designed to establish warp gates in distant sectors.",
        "acquisition_methods": ["special_construction"],
        # Canon ship-roster.md:52 — Warp Jumper requires nova_scientific_institute ≥ HONORED ONLY
        "faction_requirements": {"nova_scientific_institute": "HONORED"}
    },
    # ------------------------------------------------------------------
    # NPC-only police hulls (police-forces.md "Interdictor hulls").
    # Canon-given numbers are exact (hull/shields/speed/evasion/scanner/
    # attack/defense). Canon is silent on the remaining NOT NULL spec
    # columns — those MIRROR the DEFENDER entry above, each flagged with
    # a PLACEHOLDER comment, pending DECISIONS.
    # acquisition_methods is empty and is_npc_only is True: players can
    # never purchase, capture, salvage, or claim these hulls
    # (ERR_NPC_ONLY_HULL at the registry/purchase layer).
    # ------------------------------------------------------------------
    ShipType.NPC_MARSHAL_INTERDICTOR: {
        "is_npc_only": True,
        # NO-CANON: the ship-size axis (FEATURES/gameplay/ships.md:318-330) and
        # police-forces.md "Interdictor hulls" assign NO size to the NPC-only
        # Interdictor hulls. They are never hangared or towed (those are
        # player mechanics; ERR_NPC_ONLY_HULL blocks transfer), so leaving
        # ship_size NULL is correct and non-inventive. Explicit None keeps the
        # spec covered without fabricating canon.
        "ship_size": None,
        "base_cost": 0,  # NPC special-issue — never sold, no market price to invent
        "speed": 1.5,  # canon police-forces.md
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "turn_cost": 1,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "attack_turn_cost": 18,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_cargo": 400,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_colonists": 8,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_drones": 6,
        "max_shields": 800,  # canon
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "shield_recharge_rate": 20.0,
        "hull_points": 1200,  # canon
        "evasion": 35,  # canon
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": False,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 6,  # canon
        "attack_rating": 35,  # canon
        "defense_rating": 50,  # canon
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "construction_time": 5,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "fuel_efficiency": 70,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_upgrade_levels": {
            "ENGINE": 3,
            "CARGO_HOLD": 2,
            "SHIELD": 4,
            "HULL": 4,
            "SENSOR": 3,
            "DRONE_BAY": 3,
            "GENESIS_CONTAINMENT": 2,
            "MAINTENANCE_SYSTEM": 3
        },
        # Inert v1 — Interdictor Field deferred (police-forces.md:121-127);
        # Contraband Scanner detection formula also deferred (police-forces.md:144).
        "special_abilities": ["interdictor_field", "contraband_scanner"],
        "description": "Federation Police special-issue pursuit hull, built for sustained pursuit through Federation Zone sectors. Outguns a Defender in single combat but is no fleet flagship. NPC-only: never sold, salvaged, or claimed.",
        "acquisition_methods": [],
        "faction_requirements": None
    },
    ShipType.NPC_SENTINEL_INTERDICTOR: {
        "is_npc_only": True,
        # NO-CANON: see NPC_MARSHAL_INTERDICTOR above — canon assigns the
        # NPC-only Interdictor hulls no ship-size; ship_size stays NULL
        # (never hangared/towed; ERR_NPC_ONLY_HULL blocks transfer).
        "ship_size": None,
        "base_cost": 0,  # NPC special-issue — never sold, no market price to invent
        "speed": 1.5,  # canon police-forces.md
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "turn_cost": 1,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only)
        # — mirrors DEFENDER's 18; open question: Marshal 18 vs Sentinel ~25
        # differentiation (tougher hull, harder target) pending DECISIONS
        "attack_turn_cost": 18,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_cargo": 400,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_colonists": 8,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_drones": 6,
        "max_shields": 1000,  # canon
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "shield_recharge_rate": 20.0,
        "hull_points": 1500,  # canon
        "evasion": 40,  # canon
        "genesis_compatible": False,
        "max_genesis_devices": 0,
        "warp_compatible": False,
        "warp_creation_capable": False,
        "quantum_jump_capable": False,
        "scanner_range": 7,  # canon
        "attack_rating": 40,  # canon
        "defense_rating": 60,  # canon
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "maintenance_rate": 0.0,  # DEAD/UNUSED — neutral seed (canon decay uses by-hull-class table; see models/ship.py)
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "construction_time": 5,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "fuel_efficiency": 70,
        # PLACEHOLDER: canon silent (police-forces.md gives combat stats only) — mirrors DEFENDER; pending DECISIONS
        "max_upgrade_levels": {
            "ENGINE": 3,
            "CARGO_HOLD": 2,
            "SHIELD": 4,
            "HULL": 4,
            "SENSOR": 3,
            "DRONE_BAY": 3,
            "GENESIS_CONTAINMENT": 2,
            "MAINTENANCE_SYSTEM": 3
        },
        # Inert v1 — Interdictor Field deferred (police-forces.md:121-127);
        # Beacon Disruptor and Concord Authorization likewise inert until
        # warp-gate construction / gate-toll slices land.
        "special_abilities": ["interdictor_field", "beacon_disruptor", "concord_authorization"],
        "description": "Nexus Sentinel Corps special-issue hull. Tougher than the Marshal Interdictor — its mandate is hub-level governance of the Central Nexus, holding the line against well-equipped gate-builders. NPC-only: never sold, salvaged, or claimed.",
        "acquisition_methods": [],
        "faction_requirements": None
    }
}


# ----------------------------------------------------------------------------
# SHIP-MODS slot grid — per-hull module_slots layouts (SHIP-MODS-MASTER §3;
# WO-SM-1). Slot COUNT keys off ShipSpecification.ship_size via a LITERAL table
# {small:3, medium:4, large:6}; CAPITAL is hand-set 8; tiny/Escape-Pod 0; NULL
# ship_size (NPC-only Interdictor hulls) → 0. WHICH slot indices are
# supercharged / class-locked is hand-authored per hull for distinct identity.
# (x,y) are authored as a sensible grid FROM DAY ONE even though adjacency is
# Phase B (§9.2) — so adjacency becomes a behaviour toggle, never a re-migration.
# requires=null for every kernel slot (the Citizen `requires` seam ships as data,
# default-open; SHIP-MODS-MASTER §10 / §9.2 example shows the seam but the kernel
# leaves it null).
# ----------------------------------------------------------------------------

# Literal size→count table (§3). NEVER computed from size_units (size_units_for()
# raises on CAPITAL by design — fact 2); CAPITAL/tiny/None are handled explicitly
# in _build_module_slots, never falling through this table.
_SLOT_COUNT_BY_SIZE = {
    ShipSize.SMALL: 3,
    ShipSize.MEDIUM: 4,
    ShipSize.LARGE: 6,
}

# Hand-authored per-hull super-slot indices + the single class-locked slot
# (index → class) per §3. A hull absent here = no class-lock. Super indices are
# placed first / spread across the grid for future adjacency.
_SHIP_MODS_LAYOUT = {
    # tiny / Escape Pod → 0 slots (not a customization target — anti-grief).
    ShipType.ESCAPE_POD:    {"super": [],        "locked": {}},
    # small → 3 slots (cols 3, rows 1), 1 super.
    ShipType.SCOUT_SHIP:    {"super": [0],       "locked": {}},
    ShipType.FAST_COURIER:  {"super": [0],       "locked": {}},
    # Citizen Clipper: SMALL baseline (slots 0,1,2 open, non-super) + 1 EXTRA
    # slot (index 3) that is the hull's ONLY super slot AND class-locked to
    # "maintenance" (P2W firewall: the extra capacity is utility-fenced — no open
    # super slot, unlike free SMALL hulls' super [0]). 4 slots total.
    # WO-GC-C leg 4 — lapse-neutralization firewall: the EXTRA slot (index 3) is
    # the citizen perk, so it carries `requires: "citizen"`. While a Citizen lapses
    # this slot's module bakes to 0 (its effects go inert via _apply_module_effects);
    # the hull persists + stays flyable; re-subscribe re-bakes and restores it.
    ShipType.CITIZEN_CLIPPER: {"super": [3],     "locked": {3: "maintenance"},
                               "requires": {3: "citizen"}},
    # medium → 4 slots (cols 2, rows 2), 1 super.
    ShipType.LIGHT_FREIGHTER: {"super": [0],     "locked": {}},
    # Defender: 4 slots, 1 super, 1 class-locked "combat".
    ShipType.DEFENDER:      {"super": [0],       "locked": {3: "combat"}},
    # large → 6 slots (cols 3, rows 2), 2 super (spread: i0 + i4).
    ShipType.CARGO_HAULER:  {"super": [0, 4],    "locked": {}},
    ShipType.COLONY_SHIP:   {"super": [0, 4],    "locked": {}},
    ShipType.WARP_JUMPER:   {"super": [0, 4],    "locked": {}},
    # capital → 8 slots (cols 4, rows 2, hand-set), 3 super (spread: i0/i2/i5),
    # 1 class-locked "fleet".
    ShipType.CARRIER:       {"super": [0, 2, 5], "locked": {7: "fleet"}},
}


def _build_module_slots(ship_type: ShipType, ship_size) -> dict:
    """Build the §9.2 module_slots lattice for a ShipType.

    Slot count from the §3 literal size table (CAPITAL hand-set 8; tiny / NULL
    ship_size → 0). Grid dimensions chosen sensibly (3→3x1, 4→2x2, 6→3x2,
    8→4x2). Super / class-lock indices are hand-authored in _SHIP_MODS_LAYOUT;
    requires stays null for the kernel.
    """
    layout = _SHIP_MODS_LAYOUT.get(ship_type, {"super": [], "locked": {}})

    # Resolve slot count — explicit per-tier branches, never falling through the
    # literal table for capital/tiny/None (fact 2: size_units_for() raises on
    # CAPITAL by design).
    if ship_type == ShipType.CITIZEN_CLIPPER:
        # SMALL baseline (3) + 1 EXTRA maintenance-locked slot = 4 (P2W firewall:
        # extra capacity is utility breadth, not a combat/income axis). Branches
        # BEFORE the size table so the SMALL→3 lookup never undercounts it.
        count = 4
    elif ship_size == ShipSize.CAPITAL:
        count = 8  # hand-set (no size_unit)
    elif ship_size is None or ship_size == ShipSize.TINY:
        count = 0  # NPC-only hulls + Escape Pod → zero slots
    else:
        count = _SLOT_COUNT_BY_SIZE.get(ship_size, 0)

    # Sensible grid dimensions per count.
    cols_by_count = {0: 0, 3: 3, 4: 2, 6: 3, 8: 4}
    cols = cols_by_count.get(count, 0)
    rows = (count + cols - 1) // cols if cols else 0

    super_idx = set(layout["super"])
    locked = layout["locked"]
    # WO-GC-C leg 4 — per-slot Citizen seam. Hulls with no "requires" map → empty
    # → every slot resolves None (default open), unchanged. Only the Citizen
    # Clipper's extra slot (index 3) carries "citizen".
    requires_map = layout.get("requires", {})

    # Firewall guard (WO-GC-C reviewer LOW): a super / class-lock index that
    # exceeds the slot count would SILENTLY vanish from the lattice (range(count))
    # — e.g. if the CITIZEN_CLIPPER count=4 branch were ever dropped, slot 3's
    # maintenance-lock would disappear and re-open the firewall gap with no error.
    # Fail loud at seed time instead.
    _max_layout_idx = max([*super_idx, *locked.keys()], default=-1)
    if _max_layout_idx >= count:
        raise ValueError(
            f"_build_module_slots[{ship_type}]: layout index {_max_layout_idx} >= "
            f"slot count {count} — would drop a super/class-locked slot (firewall guard)"
        )

    slots = []
    for i in range(count):
        slots.append({
            "i": i,
            "x": (i % cols) if cols else 0,
            "y": (i // cols) if cols else 0,
            "super": i in super_idx,
            "class": locked.get(i),   # None unless this index is class-locked
            "requires": requires_map.get(i),  # Citizen seam (WO-GC-C leg 4): None unless gated
        })

    return {"v": 1, "cols": cols, "rows": rows, "slots": slots}


def seed_ship_module_slots(db: Session) -> None:
    """Idempotent boot upserter for SHIP-MODS module_slots (SHIP-MODS-MASTER §3).

    Per ShipType, build the §9.2 slot lattice (count/super/class-lock per the §3
    roster, with hand-authored (x,y)) and UPSERT it onto the matching
    ShipSpecification row. Match/conflict key is ShipType (ship_specifications.type
    is UNIQUE). Safe to re-run: re-authors module_slots in place every boot, so a
    layout re-tune in code lands on the next restart. Specs missing entirely are
    skipped here (seed_ship_specifications creates them first; this runs after).
    NPC-only / NULL ship_size + Escape Pod resolve to a zero-slot grid.
    """
    logger.info("Starting SHIP-MODS module_slots seeding...")
    upserted = 0

    for ship_type in ShipType:
        spec = db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
        if spec is None:
            # Spec row absent — seed_ship_specifications owns creation; skip.
            logger.warning(
                "No ShipSpecification for %s — skipping module_slots upsert",
                ship_type.value,
            )
            continue
        spec.module_slots = _build_module_slots(ship_type, spec.ship_size)
        upserted += 1
        logger.info(
            "Upserted module_slots for %s (%d slots)",
            ship_type.value,
            len(spec.module_slots["slots"]),
        )

    db.commit()
    logger.info("SHIP-MODS module_slots seeding complete: %d upserted", upserted)


def seed_ship_specifications(db: Session) -> None:
    """Seed ship specifications into the database"""
    logger.info("Starting ship specifications seeding...")

    seeded_count = 0
    updated_count = 0

    for ship_type, spec_data in SHIP_SPECIFICATIONS.items():
        # [NO-CANON] B3: merge the per-hull combat-mitigation values
        # (shield_resistance / armor_rating) onto this spec's data so they flow
        # through BOTH the create (**spec_data) and the update (setattr) paths
        # below. A shallow copy keeps SHIP_SPECIFICATIONS (module-level) pristine.
        # Idempotent: re-running re-sets the same values; ships present without
        # these keys get them on the next boot (update branch). Defaults to the
        # column default (0.0) for any hull absent from the table.
        spec_data = {
            **spec_data,
            **_NO_CANON_MITIGATION.get(
                ship_type, {"shield_resistance": 0.0, "armor_rating": 0.0}
            ),
        }

        # Check if specification already exists
        existing_spec = db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()

        if existing_spec:
            # Update existing specification
            for key, value in spec_data.items():
                if hasattr(existing_spec, key):
                    setattr(existing_spec, key, value)
            updated_count += 1
            logger.info(f"Updated ship specification for {ship_type.value}")
        else:
            # Create new specification
            new_spec = ShipSpecification(
                type=ship_type,
                **spec_data
            )
            db.add(new_spec)
            seeded_count += 1
            logger.info(f"Created ship specification for {ship_type.value}")

    # Commit all changes
    db.commit()

    logger.info(f"Ship specifications seeding complete: {seeded_count} created, {updated_count} updated")

    # SHIP-MODS slot grid (WO-SM-1): seed module_slots per ShipType after the
    # specs exist. Idempotent — safe to re-run every boot.
    seed_ship_module_slots(db)


def validate_ship_specifications(db: Session) -> bool:
    """Validate that all ship types have specifications"""
    logger.info("Validating ship specifications...")
    
    all_ship_types = list(ShipType)
    missing_specs = []
    
    for ship_type in all_ship_types:
        spec = db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
        
        if not spec:
            missing_specs.append(ship_type.value)
    
    if missing_specs:
        logger.error(f"Missing ship specifications for: {', '.join(missing_specs)}")
        return False
    
    logger.info("All ship types have specifications")
    return True


if __name__ == "__main__":
    # For testing purposes
    from src.core.database import get_db

    db = next(get_db())
    try:
        seed_ship_specifications(db)
        validate_ship_specifications(db)
    finally:
        db.close()