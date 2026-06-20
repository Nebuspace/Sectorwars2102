"""
Ship Specifications Seeder
Seeds the database with ship specification data based on SHIP_TYPES.md documentation
"""

from sqlalchemy.orm import Session
from src.models.ship import ShipSpecification, ShipType
import logging

logger = logging.getLogger(__name__)

# Ship specifications based on DOCS/FEATURES/SHIP_TYPES.md
SHIP_SPECIFICATIONS = {
    ShipType.ESCAPE_POD: {
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
        "maintenance_rate": 0.1,
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
        "maintenance_rate": 0.05,
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
        "maintenance_rate": 0.08,
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
        "maintenance_rate": 0.12,
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
    ShipType.SCOUT_SHIP: {
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
        "maintenance_rate": 0.15,
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
        "maintenance_rate": 0.20,
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
        "maintenance_rate": 0.15,
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
        "maintenance_rate": 0.25,
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
        "faction_requirements": {"military_faction": "TRUSTED"}
    },
    ShipType.WARP_JUMPER: {
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
        "maintenance_rate": 0.30,
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
        "faction_requirements": {"tech_guild": "HONORED", "quantum_research": "RESPECTED"}
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
        "maintenance_rate": 0.15,
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
        "maintenance_rate": 0.15,
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


def seed_ship_specifications(db: Session) -> None:
    """Seed ship specifications into the database"""
    logger.info("Starting ship specifications seeding...")
    
    seeded_count = 0
    updated_count = 0
    
    for ship_type, spec_data in SHIP_SPECIFICATIONS.items():
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
    from src.core.database import get_async_session
    
    db = next(get_db())
    try:
        seed_ship_specifications(db)
        validate_ship_specifications(db)
    finally:
        db.close()