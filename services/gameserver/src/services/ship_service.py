"""
Ship Service
Handles ship creation, destruction, and special ship mechanics
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.ship import Ship, ShipType, ShipSpecification
from src.core.ship_specifications_seeder import SHIP_SPECIFICATIONS

logger = logging.getLogger(__name__)


class ShipService:
    """Service for managing ships and ship operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_ship(self, 
                   ship_type: ShipType, 
                   owner_id: uuid.UUID, 
                   sector_id: int,
                   name: Optional[str] = None) -> Ship:
        """Create a new ship based on specifications"""
        
        # Get ship specification
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
        
        if not spec:
            raise ValueError(f"No specification found for ship type {ship_type}")
        
        # Generate ship name if not provided
        if not name:
            name = f"{ship_type.value.replace('_', ' ').title()}"
        
        # Create ship with specifications
        ship = Ship(
            name=name,
            type=ship_type,
            owner_id=owner_id,
            sector_id=sector_id,
            base_speed=spec.speed,
            current_speed=spec.speed,
            turn_cost=spec.turn_cost,
            warp_capable=spec.warp_compatible,
            
            # Initialize operational status
            is_active=True,
            maintenance={
                "condition": 100.0,
                "last_maintenance": datetime.utcnow().isoformat(),
                "next_maintenance": None,
                "repair_needed": False
            },
            
            # Initialize cargo with the spec's hold size — an empty dict
            # made every purchased ship fall back to the default capacity
            # of 50 (a Cargo Hauler shipped with a rowboat's hold)
            cargo={"capacity": spec.max_cargo, "used": 0, "contents": {}},
            
            # Initialize combat stats based on specifications
            combat={
                "shields": spec.max_shields,
                "max_shields": spec.max_shields,
                "shield_recharge_rate": spec.shield_recharge_rate,
                "hull": spec.hull_points,
                "max_hull": spec.hull_points,
                "evasion": spec.evasion,
                "attack_rating": spec.attack_rating,
                "defense_rating": spec.defense_rating
            },
            
            # Combat turn cost
            attack_turn_cost=getattr(spec, 'attack_turn_cost', None),

            # Genesis and equipment
            genesis_devices=0,
            max_genesis_devices=spec.max_genesis_devices,
            mines=0,
            max_mines=spec.max_drones,  # Using drones as mine capacity for simplicity

            # Special flags for Escape Pod
            is_destroyed=False,
            is_flagship=True,  # Initially the flagship
            purchase_value=spec.base_cost,
            current_value=spec.base_cost,

            # Initialize upgrades
            upgrades={},

            # Initialize equipment slots
            equipment_slots={},

            # Initialize insurance (none by default)
            insurance=None
        )
        
        # Add to database
        self.db.add(ship)
        self.db.flush()  # Get the ID
        
        logger.info(f"Created ship {ship.name} ({ship_type.value}) for player {owner_id}")
        return ship
    
    def destroy_ship(self, ship: Ship, destroyer: Optional[Player] = None, cause: str = "combat") -> Ship:
        """
        Destroy a ship and handle Escape Pod ejection if needed.
        Returns the ship the player ends up in (could be escape pod).
        """
        player = ship.owner
        
        # Check if ship is an Escape Pod - if so, it cannot be destroyed
        if ship.type == ShipType.ESCAPE_POD:
            logger.warning(f"Attempted to destroy indestructible Escape Pod for player {player.id}")
            return ship  # Return the same ship (indestructible)
        
        # Mark ship as destroyed
        ship.is_destroyed = True
        ship.is_active = False
        
        # Create or find escape pod for the player
        escape_pod = self._ensure_escape_pod(player, ship.sector_id)
        
        # Transfer emergency cargo to escape pod (10% of original cargo)
        self._transfer_emergency_cargo(ship, escape_pod)
        
        # Set escape pod as player's current ship
        player.current_ship_id = escape_pod.id
        
        # Apply insurance if available
        if player.insurance:
            compensation = self._calculate_insurance_payout(ship, player.insurance)
            if compensation > 0:
                player.credits += compensation
                logger.info(f"Applied insurance payout of {compensation} credits to player {player.id}")
        
        logger.info(f"Ship {ship.name} destroyed for player {player.id}, ejected to Escape Pod")
        return escape_pod
    
    def _ensure_escape_pod(self, player: Player, sector_id: int) -> Ship:
        """Ensure player has an escape pod, create one if needed"""
        
        # Check if player already has an escape pod
        escape_pod = self.db.query(Ship).filter(
            Ship.owner_id == player.id,
            Ship.type == ShipType.ESCAPE_POD,
            Ship.is_destroyed == False
        ).first()
        
        if escape_pod:
            # Move existing escape pod to current sector
            escape_pod.sector_id = sector_id
            escape_pod.is_active = True
            logger.info(f"Using existing Escape Pod for player {player.id}")
            return escape_pod
        
        # Create new escape pod
        escape_pod = self.create_ship(
            ship_type=ShipType.ESCAPE_POD,
            owner_id=player.id,
            sector_id=sector_id,
            name="Emergency Escape Pod"
        )
        
        logger.info(f"Created new Escape Pod for player {player.id}")
        return escape_pod
    
    def _transfer_emergency_cargo(self, destroyed_ship: Ship, escape_pod: Ship) -> None:
        """Transfer 10% of cargo from destroyed ship to escape pod"""
        if not destroyed_ship.cargo:
            return
        
        # Get escape pod cargo capacity
        escape_pod_spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ShipType.ESCAPE_POD
        ).first()
        
        if not escape_pod_spec:
            return
        
        max_cargo = escape_pod_spec.max_cargo
        current_cargo = sum(escape_pod.cargo.values()) if escape_pod.cargo else 0
        available_space = max_cargo - current_cargo
        
        if available_space <= 0:
            return
        
        # Calculate emergency cargo (10% of each resource)
        emergency_cargo = {}
        total_emergency_cargo = 0
        
        for resource, amount in destroyed_ship.cargo.items():
            emergency_amount = max(1, int(amount * 0.1))  # At least 1 unit
            emergency_cargo[resource] = emergency_amount
            total_emergency_cargo += emergency_amount
        
        # Limit to available space
        if total_emergency_cargo > available_space:
            # Proportionally reduce all cargo
            reduction_factor = available_space / total_emergency_cargo
            emergency_cargo = {
                resource: max(1, int(amount * reduction_factor))
                for resource, amount in emergency_cargo.items()
            }
        
        # Transfer cargo
        if not escape_pod.cargo:
            escape_pod.cargo = {}
        
        for resource, amount in emergency_cargo.items():
            if resource in escape_pod.cargo:
                escape_pod.cargo[resource] += amount
            else:
                escape_pod.cargo[resource] = amount
        
        logger.info(f"Transferred emergency cargo to Escape Pod: {emergency_cargo}")
    
    def _calculate_insurance_payout(self, ship: Ship, insurance: Dict[str, Any]) -> int:
        """Calculate insurance payout for destroyed ship"""
        insurance_type = insurance.get("type", "NONE")
        
        if insurance_type == "PREMIUM":
            return int(ship.purchase_value * 0.9)  # 90% payout
        elif insurance_type == "STANDARD":
            return int(ship.purchase_value * 0.75)  # 75% payout
        elif insurance_type == "BASIC":
            return int(ship.purchase_value * 0.5)  # 50% payout
        else:
            return 0
    
    def is_ship_indestructible(self, ship: Ship) -> bool:
        """Check if a ship is indestructible (like Escape Pod)"""
        return ship.type == ShipType.ESCAPE_POD
    
    def get_ship_specifications(self, ship_type: ShipType) -> Optional[ShipSpecification]:
        """Get ship specifications for a given ship type"""
        return self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
    
    def repair_ship(self, ship: Ship, repair_percentage: float = 100.0) -> Dict[str, Any]:
        """Repair a ship's hull and shields"""
        if ship.is_destroyed:
            return {"success": False, "message": "Cannot repair destroyed ship"}
        
        if ship.type == ShipType.ESCAPE_POD:
            # Escape pods have minimal repair needs
            ship.combat["hull"] = ship.combat["max_hull"]
            ship.combat["shields"] = ship.combat["max_shields"]
            return {"success": True, "message": "Escape Pod systems restored"}
        
        # Get current combat stats
        combat = ship.combat
        
        # Calculate repair amounts
        hull_repair = int((combat["max_hull"] - combat["hull"]) * (repair_percentage / 100.0))
        shield_repair = int((combat["max_shields"] - combat["shields"]) * (repair_percentage / 100.0))
        
        # Apply repairs
        combat["hull"] = min(combat["max_hull"], combat["hull"] + hull_repair)
        combat["shields"] = min(combat["max_shields"], combat["shields"] + shield_repair)
        
        # Update maintenance
        if "maintenance" not in ship.maintenance:
            ship.maintenance = {}
        ship.maintenance["last_maintenance"] = datetime.utcnow().isoformat()
        ship.maintenance["condition"] = min(100.0, ship.maintenance.get("condition", 0) + repair_percentage)
        
        return {
            "success": True,
            "message": f"Ship repaired: +{hull_repair} hull, +{shield_repair} shields",
            "hull_repaired": hull_repair,
            "shields_repaired": shield_repair
        }