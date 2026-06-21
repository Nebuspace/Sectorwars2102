"""
Drone management service for Sectorwars2102.

Handles drone creation, deployment, combat, and strategy.
"""

from uuid import UUID
from datetime import datetime
from typing import List, Optional, Dict, Any
import json
import random
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

from src.models.drone import Drone, DroneDeployment, DroneCombat, DroneType, DroneStatus
from src.models.player import Player
from src.models.sector import Sector
from src.models.team import Team


class DroneService:
    """Service for managing drones and their operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        
    async def create_drone(
        self,
        player_id: UUID,
        drone_type: str,
        name: Optional[str] = None,
        team_id: Optional[UUID] = None
    ) -> Drone:
        """
        Create a new drone for a player.
        
        Args:
            player_id: ID of the player creating the drone
            drone_type: Type of drone to create
            name: Optional custom name for the drone
            team_id: Optional team ID to assign the drone to
            
        Returns:
            The created drone
        """
        # Validate drone type
        if drone_type not in [dt.value for dt in DroneType]:
            raise ValueError(f"Invalid drone type: {drone_type}")
            
        # Set base stats based on drone type
        base_stats = self._get_base_stats(drone_type)
        
        drone = Drone(
            player_id=player_id,
            team_id=team_id,
            drone_type=drone_type,
            name=name,
            **base_stats
        )
        
        self.session.add(drone)
        await self.session.commit()
        await self.session.refresh(drone)
        
        return drone
        
    def _get_base_stats(self, drone_type: str) -> Dict[str, Any]:
        """Get base stats for a drone type."""
        stats = {
            DroneType.ATTACK.value: {
                "health": 80,
                "max_health": 80,
                "attack_power": 20,
                "defense_power": 5,
                "speed": 1.5,
                "abilities": "precision_strike,rapid_fire"
            },
            DroneType.DEFENSE.value: {
                "health": 150,
                "max_health": 150,
                "attack_power": 8,
                "defense_power": 20,
                "speed": 0.8,
                "abilities": "shield_boost,area_defense"
            },
            DroneType.SCOUT.value: {
                "health": 60,
                "max_health": 60,
                "attack_power": 5,
                "defense_power": 8,
                "speed": 2.0,
                "abilities": "enhanced_sensors,stealth"
            },
            DroneType.MINING.value: {
                "health": 100,
                "max_health": 100,
                "attack_power": 3,
                "defense_power": 10,
                "speed": 1.0,
                "abilities": "resource_extraction,cargo_boost"
            },
            DroneType.REPAIR.value: {
                "health": 90,
                "max_health": 90,
                "attack_power": 2,
                "defense_power": 12,
                "speed": 1.2,
                "abilities": "repair_beam,shield_recharge"
            }
        }
        
        return stats.get(drone_type, stats[DroneType.DEFENSE.value])
        
    async def deploy_drone(
        self,
        drone_id: UUID,
        sector_id: UUID,
        deployment_type: str = "defense",
        target_id: Optional[UUID] = None
    ) -> DroneDeployment:
        """
        Deploy a drone to a sector.
        
        Args:
            drone_id: ID of the drone to deploy
            sector_id: ID of the sector to deploy to
            deployment_type: Type of deployment (defense, patrol, mining, etc.)
            target_id: Optional target ID for specific missions
            
        Returns:
            The deployment record
        """
        # Get the drone and update its status
        drone = await self.session.get(Drone, drone_id)
        if not drone:
            raise ValueError("Drone not found")
            
        if drone.status == DroneStatus.DESTROYED.value:
            raise ValueError("Cannot deploy destroyed drone")
            
        # Recall any active deployment
        await self.recall_drone(drone_id)
        
        # Update drone status and location
        drone.status = DroneStatus.DEPLOYED.value
        drone.sector_id = sector_id
        drone.deployed_at = datetime.utcnow()
        
        # Create deployment record
        deployment = DroneDeployment(
            drone_id=drone_id,
            player_id=drone.player_id,
            sector_id=sector_id,
            deployment_type=deployment_type,
            target_id=target_id,
            is_active=True
        )
        
        self.session.add(deployment)
        await self.session.commit()
        await self.session.refresh(deployment)
        
        return deployment
        
    async def recall_drone(self, drone_id: UUID) -> Optional[DroneDeployment]:
        """
        Recall a deployed drone.
        
        Args:
            drone_id: ID of the drone to recall
            
        Returns:
            The updated deployment record if one exists
        """
        # Find active deployment
        result = await self.session.execute(
            select(DroneDeployment)
            .where(and_(
                DroneDeployment.drone_id == drone_id,
                DroneDeployment.is_active == True
            ))
        )
        deployment = result.scalar_one_or_none()
        
        if deployment:
            deployment.is_active = False
            deployment.recalled_at = datetime.utcnow()
            
            # Update drone status. Recall is instantaneous in this API (sector_id
            # is cleared synchronously), and there is no scheduler/tick anywhere
            # that completes a RETURNING drone's transit back to base. Leaving the
            # drone in RETURNING was a terminal dead-end: nothing ever moved it
            # back to IDLE, so a recalled drone was permanently stuck in a phantom
            # state (sector_id None but status not idle), passing the
            # "undeployed" re-deploy filter while never being cleanly available.
            # The correct terminal state for a completed recall is IDLE ("created
            # but not deployed to any sector" — see DroneStatus.IDLE).
            drone = await self.session.get(Drone, drone_id)
            if drone and drone.status != DroneStatus.DESTROYED.value:
                drone.status = DroneStatus.IDLE.value
                drone.sector_id = None
                
            await self.session.commit()
            
        return deployment
        
    async def get_sector_drones(self, sector_id: UUID) -> List[Drone]:
        """Get all active drones in a sector."""
        result = await self.session.execute(
            select(Drone)
            .where(and_(
                Drone.sector_id == sector_id,
                Drone.status != DroneStatus.DESTROYED.value
            ))
            .options(selectinload(Drone.player))
        )
        return result.scalars().all()
        
    async def initiate_combat(
        self,
        attacker_id: UUID,
        defender_id: UUID,
        sector_id: UUID
    ) -> DroneCombat:
        """
        Initiate combat between two drones.
        
        Args:
            attacker_id: ID of the attacking drone
            defender_id: ID of the defending drone
            sector_id: ID of the sector where combat occurs
            
        Returns:
            The combat record
        """
        # Get both drones
        attacker = await self.session.get(Drone, attacker_id)
        defender = await self.session.get(Drone, defender_id)
        
        if not attacker or not defender:
            raise ValueError("One or both drones not found")
            
        # Update drone statuses
        attacker.status = DroneStatus.COMBAT.value
        defender.status = DroneStatus.COMBAT.value
        attacker.last_action = datetime.utcnow()
        defender.last_action = datetime.utcnow()
        
        # Create combat record
        combat = DroneCombat(
            attacker_drone_id=attacker_id,
            defender_drone_id=defender_id,
            sector_id=sector_id
        )
        
        self.session.add(combat)
        
        # Simulate combat
        combat_log = await self._simulate_combat(attacker, defender, combat)
        combat.combat_log = json.dumps(combat_log)
        
        await self.session.commit()
        await self.session.refresh(combat)
        
        return combat
        
    async def _simulate_combat(
        self,
        attacker: Drone,
        defender: Drone,
        combat: DroneCombat
    ) -> List[Dict[str, Any]]:
        """
        Simulate combat between two drones.
        
        Returns a log of combat events.
        """
        log = []
        round_num = 0
        max_rounds = 20
        
        while (
            attacker.health > 0 and 
            defender.health > 0 and 
            round_num < max_rounds
        ):
            round_num += 1
            
            # Calculate damage based on stats and randomness
            attacker_damage = max(1, attacker.attack_power - defender.defense_power // 2 + random.randint(-3, 3))
            defender_damage = max(1, defender.attack_power - attacker.defense_power // 2 + random.randint(-3, 3))
            
            # Apply speed modifiers (faster drones sometimes attack twice)
            if attacker.speed > defender.speed and random.random() < 0.3:
                attacker_damage *= 1.5
                
            if defender.speed > attacker.speed and random.random() < 0.3:
                defender_damage *= 1.5
                
            # Apply damage
            defender_destroyed = defender.take_damage(int(attacker_damage))
            attacker_destroyed = attacker.take_damage(int(defender_damage))
            
            # Update combat stats
            combat.attacker_damage_dealt += int(attacker_damage)
            combat.defender_damage_dealt += int(defender_damage)
            
            # Log the round
            log.append({
                "round": round_num,
                "attacker_damage": int(attacker_damage),
                "defender_damage": int(defender_damage),
                "attacker_health": attacker.health,
                "defender_health": defender.health
            })
            
            # Check for destruction
            if defender_destroyed or attacker_destroyed:
                break
                
        # Determine winner
        combat.rounds = round_num
        combat.ended_at = datetime.utcnow()
        
        if attacker.health > 0 and defender.health == 0:
            combat.winner_drone_id = attacker.id
            attacker.kills += 1
            attacker.status = DroneStatus.DEPLOYED.value
            log.append({"result": "attacker_victory"})
            
        elif defender.health > 0 and attacker.health == 0:
            combat.winner_drone_id = defender.id
            defender.kills += 1
            defender.status = DroneStatus.DEPLOYED.value
            log.append({"result": "defender_victory"})
            
        else:
            # Draw or timeout
            if attacker.health > 0:
                attacker.status = DroneStatus.DEPLOYED.value
            if defender.health > 0:
                defender.status = DroneStatus.DEPLOYED.value
            log.append({"result": "draw"})
            
        # Update battle counts
        attacker.battles_fought += 1
        defender.battles_fought += 1
        attacker.damage_dealt += combat.attacker_damage_dealt
        defender.damage_dealt += combat.defender_damage_dealt
        
        return log
        
    async def repair_drone(self, drone_id: UUID, repair_amount: int) -> Drone:
        """
        Repair a damaged drone.
        
        Args:
            drone_id: ID of the drone to repair
            repair_amount: Amount of health to restore
            
        Returns:
            The repaired drone
        """
        drone = await self.session.get(Drone, drone_id)
        if not drone:
            raise ValueError("Drone not found")
            
        if drone.status == DroneStatus.DESTROYED.value:
            raise ValueError("Cannot repair destroyed drone")
            
        drone.repair(repair_amount)
        await self.session.commit()
        await self.session.refresh(drone)
        
        return drone
        
    async def upgrade_drone(self, drone_id: UUID) -> Drone:
        """
        Upgrade a drone to the next level.
        
        Args:
            drone_id: ID of the drone to upgrade
            
        Returns:
            The upgraded drone
        """
        drone = await self.session.get(Drone, drone_id)
        if not drone:
            raise ValueError("Drone not found")
            
        # Increase level and stats
        drone.level += 1
        upgrade_factor = 1.1  # 10% increase per level
        
        drone.max_health = int(drone.max_health * upgrade_factor)
        drone.health = drone.max_health  # Full heal on upgrade
        drone.attack_power = int(drone.attack_power * upgrade_factor)
        drone.defense_power = int(drone.defense_power * upgrade_factor)
        drone.speed = round(drone.speed * 1.05, 2)  # 5% speed increase
        
        await self.session.commit()
        await self.session.refresh(drone)
        
        return drone
        
    async def get_player_drones(
        self,
        player_id: UUID,
        include_destroyed: bool = False
    ) -> List[Drone]:
        """Get all drones owned by a player."""
        query = select(Drone).where(Drone.player_id == player_id)
        
        if not include_destroyed:
            query = query.where(Drone.status != DroneStatus.DESTROYED.value)
            
        result = await self.session.execute(query)
        return result.scalars().all()
        
    async def get_team_drones(
        self,
        team_id: UUID,
        include_destroyed: bool = False
    ) -> List[Drone]:
        """Get all drones assigned to a team."""
        query = select(Drone).where(Drone.team_id == team_id)
        
        if not include_destroyed:
            query = query.where(Drone.status != DroneStatus.DESTROYED.value)
            
        result = await self.session.execute(query)
        return result.scalars().all()
        
    async def get_drone_deployments(
        self,
        drone_id: Optional[UUID] = None,
        player_id: Optional[UUID] = None,
        sector_id: Optional[UUID] = None,
        active_only: bool = True
    ) -> List[DroneDeployment]:
        """Get drone deployments with optional filters."""
        query = select(DroneDeployment)
        
        if drone_id:
            query = query.where(DroneDeployment.drone_id == drone_id)
        if player_id:
            query = query.where(DroneDeployment.player_id == player_id)
        if sector_id:
            query = query.where(DroneDeployment.sector_id == sector_id)
        if active_only:
            query = query.where(DroneDeployment.is_active == True)
            
        result = await self.session.execute(query.options(selectinload(DroneDeployment.drone)))
        return result.scalars().all()
        
    async def get_combat_history(
        self,
        drone_id: Optional[UUID] = None,
        sector_id: Optional[UUID] = None,
        limit: int = 10
    ) -> List[DroneCombat]:
        """Get combat history for a drone or sector."""
        query = select(DroneCombat)
        
        if drone_id:
            query = query.where(or_(
                DroneCombat.attacker_drone_id == drone_id,
                DroneCombat.defender_drone_id == drone_id
            ))
        if sector_id:
            query = query.where(DroneCombat.sector_id == sector_id)
            
        query = query.order_by(DroneCombat.started_at.desc()).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()