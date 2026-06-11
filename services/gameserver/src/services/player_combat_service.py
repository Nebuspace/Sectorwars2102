"""
Combat service for managing player vs player/NPC combat.

This service handles combat initiation, round simulation,
damage calculations, and loot distribution.
"""

from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID, uuid4
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
import random
import math
import logging

from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.combat_log import CombatLog, CombatStats, CombatOutcome
from src.models.sector import Sector
from src.models.planet import Planet
from src.models.station import Station
from src.models.drone import Drone, DroneDeployment, DroneCombat, DroneType, DroneStatus
from src.services.drone_service import DroneService

logger = logging.getLogger(__name__)


class PlayerCombatService:
    """Service for managing combat operations."""
    
    def __init__(self, db: Session):
        self.db = db
        self.drone_service = DroneService(db)
        
    def initiate_combat(
        self,
        attacker_id: UUID,
        target_type: str,
        target_id: UUID
    ) -> Dict[str, Any]:
        """Initiate combat between attacker and target."""
        # Validate attacker
        attacker = self.db.query(Player).filter(Player.id == attacker_id).first()
        if not attacker:
            return {
                "combatId": None,
                "status": "error",
                "message": "Attacker not found"
            }
            
        # Get attacker's ship
        attacker_ship = self.db.query(Ship).filter(
            and_(Ship.player_id == attacker_id, Ship.is_active == True)
        ).first()
        
        if not attacker_ship:
            return {
                "combatId": None,
                "status": "error",
                "message": "No active ship found"
            }
            
        # Validate target based on type
        if target_type == "ship":
            target_ship = self.db.query(Ship).filter(Ship.id == target_id).first()
            if not target_ship:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target ship not found"
                }
                
            # Check if ships are in same sector
            if attacker_ship.current_sector_id != target_ship.current_sector_id:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target ship is not in the same sector"
                }
                
            # Create combat log
            combat_log = self._create_ship_combat(attacker, attacker_ship, target_ship)
            
        elif target_type == "planet":
            planet = self.db.query(Planet).filter(Planet.id == target_id).first()
            if not planet:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target planet not found"
                }
                
            # Check if planet is in same sector
            if attacker_ship.current_sector_id != planet.sector_id:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target planet is not in the same sector"
                }
                
            # Create combat log for planetary assault
            combat_log = self._create_planet_combat(attacker, attacker_ship, planet)
            
        elif target_type == "port":
            # The API contract admits "port" (CombatEngageRequest); this branch
            # previously matched "station" and referenced an undefined `port`
            # variable, so port raids always fell through to the error case
            station = self.db.query(Station).filter(Station.id == target_id).first()
            if not station:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target port not found"
                }

            # Check if port is in same sector
            if attacker_ship.current_sector_id != station.sector_id:
                return {
                    "combatId": None,
                    "status": "error",
                    "message": "Target port is not in the same sector"
                }

            # Create combat log for port raid
            combat_log = self._create_port_combat(attacker, attacker_ship, station)
            
        else:
            return {
                "combatId": None,
                "status": "error",
                "message": "Invalid target type"
            }
            
        self.db.add(combat_log)
        self.db.commit()
        self.db.refresh(combat_log)
        
        # Simulate first round
        self._simulate_combat_round(combat_log)
        
        return {
            "combatId": str(combat_log.id),
            "status": "initiated",
            "message": f"Combat initiated against {target_type}"
        }
        
    def get_combat_status(self, combat_id: UUID) -> Dict[str, Any]:
        """Get current status of a combat."""
        combat_log = self.db.query(CombatLog).filter(CombatLog.id == combat_id).first()
        if not combat_log:
            raise ValueError(f"Combat {combat_id} not found")
            
        # Get combat stats
        stats = self.db.query(CombatStats).filter(
            CombatStats.combat_log_id == combat_id
        ).order_by(CombatStats.round_number).all()
        
        rounds = []
        for stat in stats:
            rounds.append({
                "round": stat.round_number,
                "attackerHits": stat.attacker_hits,
                "defenderHits": stat.defender_hits,
                "attackerDamage": stat.attacker_damage,
                "defenderDamage": stat.defender_damage,
                "attackerShields": stat.attacker_shields_remaining,
                "defenderShields": stat.defender_shields_remaining,
                "attackerArmor": stat.attacker_armor_remaining,
                "defenderArmor": stat.defender_armor_remaining,
                "criticalHit": stat.critical_hit,
                "specialEvent": stat.special_event
            })
            
        # Determine if combat is complete
        status = "ongoing"
        winner = None
        
        if combat_log.outcome != CombatOutcome.ONGOING:
            status = "completed"
            if combat_log.outcome == CombatOutcome.ATTACKER_WIN:
                winner = str(combat_log.attacker_id)
            elif combat_log.outcome == CombatOutcome.DEFENDER_WIN:
                winner = str(combat_log.defender_id)
            elif combat_log.outcome == CombatOutcome.DRAW:
                winner = "draw"
                
        # If ongoing and no recent activity, simulate next round
        if status == "ongoing":
            last_round = stats[-1] if stats else None
            if not last_round or (datetime.utcnow() - last_round.timestamp).seconds > 5:
                self._simulate_combat_round(combat_log)
                # Re-fetch status after simulation
                return self.get_combat_status(combat_id)
                
        return {
            "status": status,
            "rounds": rounds,
            "winner": winner,
            "combatDuration": combat_log.combat_duration,
            "creditsLooted": combat_log.credits_looted or 0,
            "cargoLooted": combat_log.cargo_looted or []
        }
        
    def _create_ship_combat(
        self,
        attacker: Player,
        attacker_ship: Ship,
        target_ship: Ship
    ) -> CombatLog:
        """Create combat log for ship vs ship combat."""
        combat_log = CombatLog(
            attacker_id=attacker.id,
            defender_id=target_ship.player_id,
            attacker_ship_id=attacker_ship.id,
            defender_ship_id=target_ship.id,
            attacker_ship_name=attacker_ship.name,
            defender_ship_name=target_ship.name,
            attacker_ship_type=attacker_ship.type,
            defender_ship_type=target_ship.type,
            sector_id=attacker_ship.current_sector_id,
            combat_type="ship_vs_ship",
            attacker_drones=attacker_ship.drones,
            defender_drones=target_ship.drones,
            outcome=CombatOutcome.ONGOING,
            timestamp=datetime.utcnow()
        )
        return combat_log
        
    def _create_planet_combat(
        self,
        attacker: Player,
        attacker_ship: Ship,
        planet: Planet
    ) -> CombatLog:
        """Create combat log for planetary assault."""
        # Calculate planet defense based on owner's ships in sector
        planet_defense = self._calculate_planet_defense(planet)
        
        combat_log = CombatLog(
            attacker_id=attacker.id,
            defender_id=planet.owner_id,  # Could be None for unowned planets
            attacker_ship_id=attacker_ship.id,
            attacker_ship_name=attacker_ship.name,
            attacker_ship_type=attacker_ship.type,
            defender_ship_name=planet.name,
            defender_ship_type="planet",
            sector_id=attacker_ship.current_sector_id,
            combat_type="planetary_assault",
            attacker_drones=attacker_ship.drones,
            defender_drones=planet_defense,
            outcome=CombatOutcome.ONGOING,
            timestamp=datetime.utcnow()
        )
        return combat_log
        
    def _create_port_combat(
        self,
        attacker: Player,
        attacker_ship: Ship,
        port: Station
    ) -> CombatLog:
        """Create combat log for port raid."""
        # Calculate port defense based on class
        port_defense = self._calculate_port_defense(port)
        
        combat_log = CombatLog(
            attacker_id=attacker.id,
            defender_id=None,  # Ports are NPC controlled
            attacker_ship_id=attacker_ship.id,
            attacker_ship_name=attacker_ship.name,
            attacker_ship_type=attacker_ship.type,
            defender_ship_name=station.name,
            defender_ship_type="port",
            sector_id=attacker_ship.current_sector_id,
            combat_type="port_raid",
            attacker_drones=attacker_ship.drones,
            defender_drones=port_defense,
            outcome=CombatOutcome.ONGOING,
            timestamp=datetime.utcnow()
        )
        return combat_log
        
    def _calculate_planet_defense(self, planet: Planet) -> int:
        """Calculate planet's defense strength."""
        base_defense = 100  # Base defense for all planets
        
        # Add owner's ships in sector as defense
        if planet.owner_id:
            defender_ships = self.db.query(Ship).filter(
                and_(
                    Ship.player_id == planet.owner_id,
                    Ship.current_sector_id == planet.sector_id,
                    Ship.is_active == True
                )
            ).all()
            
            for ship in defender_ships:
                base_defense += ship.drones
                
        # Add any deployed drones
        deployments = self.db.query(DroneDeployment).filter(
            and_(
                DroneDeployment.sector_id == planet.sector_id,
                DroneDeployment.status == "active"
            )
        ).all()
        
        for deployment in deployments:
            if deployment.player.team_id == planet.owner.team_id if planet.owner else False:
                base_defense += deployment.drone_count * 20  # Each drone adds 20 defense
                
        return base_defense
        
    def _calculate_port_defense(self, port: Station) -> int:
        """Calculate port's defense strength based on class."""
        port_defenses = {
            "special": 500,
            "class_0": 0,
            "class_1": 50,
            "class_2": 100,
            "class_3": 150,
            "class_4": 200,
            "class_5": 250,
            "class_6": 300,
            "class_7": 350,
            "class_8": 400,
            "class_9": 450
        }
        
        return port_defenses.get(station.station_class, 100)
        
    def _simulate_combat_round(self, combat_log: CombatLog):
        """Simulate one round of combat."""
        # Get current ship states
        attacker_ship = self.db.query(Ship).filter(Ship.id == combat_log.attacker_ship_id).first()
        defender_ship = None
        
        if combat_log.combat_type == "ship_vs_ship":
            defender_ship = self.db.query(Ship).filter(Ship.id == combat_log.defender_ship_id).first()
            
        # Get last round stats
        last_stats = self.db.query(CombatStats).filter(
            CombatStats.combat_log_id == combat_log.id
        ).order_by(CombatStats.round_number.desc()).first()
        
        round_number = (last_stats.round_number + 1) if last_stats else 1
        
        # Initialize stats from last round or ships
        if last_stats:
            attacker_shields = last_stats.attacker_shields_remaining
            attacker_armor = last_stats.attacker_armor_remaining
            defender_shields = last_stats.defender_shields_remaining
            defender_armor = last_stats.defender_armor_remaining
        else:
            attacker_shields = attacker_ship.shields if attacker_ship else 0
            attacker_armor = attacker_ship.armor if attacker_ship else 0
            
            if defender_ship:
                defender_shields = defender_ship.shields
                defender_armor = defender_ship.armor
            else:
                # NPC targets
                defender_shields = 0
                if combat_log.combat_type == "planetary_assault":
                    defender_armor = combat_log.defender_drones * 10
                else:  # port raid
                    defender_armor = combat_log.defender_drones * 5
                    
        # Calculate hits and damage
        attacker_accuracy = 0.7 + (attacker_ship.speed / 1000) if attacker_ship else 0.7
        defender_accuracy = 0.6  # Base accuracy for NPCs
        
        if defender_ship:
            defender_accuracy = 0.7 + (defender_ship.speed / 1000)
            
        # Attacker fires
        attacker_hits = 0
        attacker_damage = 0
        if random.random() < attacker_accuracy:
            attacker_hits = 1
            base_damage = attacker_ship.guns * 10 if attacker_ship else 0
            # Critical hit chance
            if random.random() < 0.1:
                base_damage *= 2
                
            attacker_damage = base_damage
            
        # Defender fires
        defender_hits = 0
        defender_damage = 0
        if defender_armor > 0 and random.random() < defender_accuracy:
            defender_hits = 1
            if defender_ship:
                base_damage = defender_ship.guns * 10
            else:
                # NPC damage
                base_damage = combat_log.defender_drones // 10
                
            defender_damage = base_damage
            
        # Apply damage
        if attacker_damage > 0:
            if defender_shields > 0:
                shield_absorbed = min(attacker_damage, defender_shields)
                defender_shields -= shield_absorbed
                attacker_damage -= shield_absorbed
                
            defender_armor -= attacker_damage
            
        if defender_damage > 0:
            if attacker_shields > 0:
                shield_absorbed = min(defender_damage, attacker_shields)
                attacker_shields -= shield_absorbed
                defender_damage -= shield_absorbed
                
            attacker_armor -= defender_damage
            
        # Create round stats
        stats = CombatStats(
            combat_log_id=combat_log.id,
            round_number=round_number,
            attacker_hits=attacker_hits,
            defender_hits=defender_hits,
            attacker_damage=attacker_damage,
            defender_damage=defender_damage,
            attacker_shields_remaining=max(0, attacker_shields),
            defender_shields_remaining=max(0, defender_shields),
            attacker_armor_remaining=max(0, attacker_armor),
            defender_armor_remaining=max(0, defender_armor),
            critical_hit=attacker_damage > attacker_ship.guns * 15 if attacker_ship else False,
            timestamp=datetime.utcnow()
        )
        
        self.db.add(stats)
        
        # Update combat log totals
        combat_log.attacker_damage_dealt = (combat_log.attacker_damage_dealt or 0) + attacker_damage
        combat_log.defender_damage_dealt = (combat_log.defender_damage_dealt or 0) + defender_damage
        combat_log.rounds_fought = round_number
        
        # Check for combat end
        if attacker_armor <= 0 or defender_armor <= 0:
            self._end_combat(combat_log, attacker_armor, defender_armor)
            
        # Update ship states
        if attacker_ship:
            attacker_ship.shields = max(0, attacker_shields)
            attacker_ship.armor = max(0, attacker_armor)
            
        if defender_ship:
            defender_ship.shields = max(0, defender_shields)
            defender_ship.armor = max(0, defender_armor)
            
        self.db.commit()
        
    def _end_combat(self, combat_log: CombatLog, attacker_armor: int, defender_armor: int):
        """End combat and determine winner."""
        combat_log.ended_at = datetime.utcnow()
        combat_log.combat_duration = int(
            (combat_log.ended_at - combat_log.timestamp).total_seconds()
        )
        
        if attacker_armor <= 0 and defender_armor <= 0:
            combat_log.outcome = CombatOutcome.DRAW
        elif attacker_armor > 0:
            combat_log.outcome = CombatOutcome.ATTACKER_WIN
            self._process_loot(combat_log)
        else:
            combat_log.outcome = CombatOutcome.DEFENDER_WIN
            
        # Handle ship destruction
        if combat_log.combat_type == "ship_vs_ship":
            if attacker_armor <= 0 and combat_log.attacker_ship_id:
                ship = self.db.query(Ship).filter(Ship.id == combat_log.attacker_ship_id).first()
                if ship:
                    ship.is_active = False
                    ship.destroyed_at = datetime.utcnow()
                    
            if defender_armor <= 0 and combat_log.defender_ship_id:
                ship = self.db.query(Ship).filter(Ship.id == combat_log.defender_ship_id).first()
                if ship:
                    ship.is_active = False
                    ship.destroyed_at = datetime.utcnow()
                    
    def _process_loot(self, combat_log: CombatLog):
        """Process loot for the winner."""
        if combat_log.combat_type == "ship_vs_ship":
            # Loot from destroyed ship
            defender = self.db.query(Player).filter(Player.id == combat_log.defender_id).first()
            if defender:
                # Transfer some credits
                loot_credits = min(defender.credits // 10, 10000)
                defender.credits -= loot_credits
                
                attacker = self.db.query(Player).filter(Player.id == combat_log.attacker_id).first()
                if attacker:
                    attacker.credits += loot_credits
                    
                combat_log.credits_looted = loot_credits
                
        elif combat_log.combat_type == "port_raid":
            # Fixed loot based on port class
            station = self.db.query(Station).filter(
                Station.sector_id == combat_log.sector_id
            ).first()
            
            if port:
                loot_table = {
                    "class_1": 1000,
                    "class_2": 2000,
                    "class_3": 3000,
                    "class_4": 4000,
                    "class_5": 5000,
                    "class_6": 6000,
                    "class_7": 7000,
                    "class_8": 8000,
                    "class_9": 9000,
                    "special": 15000
                }
                
                loot_credits = loot_table.get(station.station_class, 1000)
                attacker = self.db.query(Player).filter(Player.id == combat_log.attacker_id).first()
                if attacker:
                    attacker.credits += loot_credits

                combat_log.credits_looted = loot_credits

    def attempt_retreat(
        self,
        combat_id: UUID,
        player_id: UUID
    ) -> Dict[str, Any]:
        """
        Attempt to retreat from an ongoing combat.

        Retreat success is based on ship speed and combat round count.
        Earlier rounds are harder to retreat from. Faster ships have
        better retreat chances.
        """
        combat_log = self.db.query(CombatLog).filter(CombatLog.id == combat_id).first()
        if not combat_log:
            return {
                "success": False,
                "message": "Combat not found"
            }

        # Verify player is the attacker in this combat
        if combat_log.attacker_id != player_id:
            return {
                "success": False,
                "message": "Only the attacker can retreat"
            }

        # Cannot retreat from completed combat
        if combat_log.outcome != CombatOutcome.ONGOING:
            return {
                "success": False,
                "message": "Combat is already over"
            }

        # Get attacker's ship for speed-based retreat calculation
        attacker_ship = self.db.query(Ship).filter(
            Ship.id == combat_log.attacker_ship_id
        ).first()

        # Base retreat chance starts at 30% and increases with rounds fought
        rounds_fought = combat_log.rounds_fought or 0
        base_chance = 0.30 + (rounds_fought * 0.10)  # +10% per round

        # Speed bonus: faster ships retreat more easily
        speed_bonus = 0.0
        if attacker_ship and attacker_ship.speed:
            speed_bonus = min(0.20, attacker_ship.speed / 500)  # Up to 20% bonus

        retreat_chance = min(0.90, base_chance + speed_bonus)  # Cap at 90%

        if random.random() < retreat_chance:
            # Retreat successful
            combat_log.outcome = CombatOutcome.ESCAPED
            combat_log.ended_at = datetime.utcnow()
            combat_log.combat_duration = int(
                (combat_log.ended_at - combat_log.timestamp).total_seconds()
            )

            self.db.commit()

            return {
                "success": True,
                "message": "Retreat successful! You escaped the combat.",
                "retreatChance": round(retreat_chance * 100)
            }
        else:
            # Retreat failed - defender gets a free attack round
            self._simulate_combat_round(combat_log)

            return {
                "success": False,
                "message": "Retreat failed! The enemy landed a hit as you tried to flee.",
                "retreatChance": round(retreat_chance * 100)
            }