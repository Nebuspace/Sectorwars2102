"""
Drone management service for Sectorwars2102.

Handles drone creation, deployment, and strategy.
"""

from uuid import UUID
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from src.models.drone import Drone, DroneDeployment, DroneType, DroneStatus
from src.models.player import Player
from src.models.sector import Sector
from src.models.team import Team
from src.models.ship import Ship, ShipSpecification


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

        # Anti-exploit: enforce the per-player drone cap (ShipSpecification
        # .max_drones for the current ship) so a player cannot spam unlimited
        # drone rows. Lock the owning player row FOR UPDATE FIRST so the
        # check-then-insert is atomic per player — two concurrent creates
        # serialize on this lock and cannot both read an under-cap count and
        # then both insert past the cap.
        locked = await self.session.execute(
            select(Player.id).where(Player.id == player_id).with_for_update()
        )
        if locked.scalar_one_or_none() is None:
            raise ValueError("Player not found")

        max_drones = await self._get_max_drones(player_id)
        current = await self._count_live_drones(player_id)
        if current + 1 > max_drones:
            raise ValueError(
                f"Drone capacity reached: your current ship holds at most "
                f"{max_drones} drone(s) (you have {current})."
            )

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

    async def _get_max_drones(self, player_id: UUID) -> int:
        """Resolve the per-player drone cap from the player's current-ship spec.

        The cap is ``ShipSpecification.max_drones`` for the player's
        ``current_ship`` (the same source the armory loadout caps use —
        armory.py /purchase reads ``spec.max_drones`` off the current ship).
        A player with no active ship has a cap of 0 (you need a ship to carry
        drones — matches the armory "You need an active ship to carry armory
        items" rule).
        """
        player = await self.session.get(Player, player_id)
        if player is None or player.current_ship_id is None:
            return 0

        ship = await self.session.get(Ship, player.current_ship_id)
        if ship is None:
            return 0

        result = await self.session.execute(
            select(ShipSpecification.max_drones).where(
                ShipSpecification.type == ship.type
            )
        )
        max_drones = result.scalar_one_or_none()
        return int(max_drones) if max_drones is not None else 0

    async def _count_live_drones(self, player_id: UUID) -> int:
        """Count a player's non-destroyed drones (the ones that occupy a cap slot)."""
        result = await self.session.execute(
            select(func.count())
            .select_from(Drone)
            .where(and_(
                Drone.player_id == player_id,
                Drone.status != DroneStatus.DESTROYED.value,
            ))
        )
        return int(result.scalar() or 0)

    async def _count_deployed_drones(
        self, player_id: UUID, exclude_drone_id: Optional[UUID] = None
    ) -> int:
        """Count a player's drones currently in the field (deployed/combat).

        ``exclude_drone_id`` omits a specific drone from the tally — used on the
        deploy path so the drone being (re)deployed is not counted against the
        cap as both the existing field drone and the new one.
        """
        conditions = [
            Drone.player_id == player_id,
            Drone.status.in_((
                DroneStatus.DEPLOYED.value,
                DroneStatus.COMBAT.value,
            )),
        ]
        if exclude_drone_id is not None:
            conditions.append(Drone.id != exclude_drone_id)
        result = await self.session.execute(
            select(func.count())
            .select_from(Drone)
            .where(and_(*conditions))
        )
        return int(result.scalar() or 0)

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

        # Anti-exploit: enforce the per-player drone cap on deploy as well as on
        # create — a player must not field more drones than their current ship's
        # ShipSpecification.max_drones. Lock the owning player row FOR UPDATE
        # FIRST so the count-then-deploy is atomic: concurrent deploys serialize
        # on this lock and cannot both pass an under-cap field count and then
        # both flip a drone to DEPLOYED past the cap. The drone being deployed
        # is excluded from the field tally (it is about to occupy one slot, not
        # two), so a no-op re-deploy of an already-fielded drone is unchanged.
        locked = await self.session.execute(
            select(Player.id).where(Player.id == drone.player_id).with_for_update()
        )
        if locked.scalar_one_or_none() is None:
            raise ValueError("Player not found")

        max_drones = await self._get_max_drones(drone.player_id)
        deployed = await self._count_deployed_drones(
            drone.player_id, exclude_drone_id=drone_id
        )
        if deployed + 1 > max_drones:
            raise ValueError(
                f"Drone deployment limit reached: your current ship can field "
                f"at most {max_drones} drone(s) (you have {deployed} deployed)."
            )

        # Recall any active deployment for this drone IN-LINE (no intermediate
        # commit). recall_drone() commits internally, which would release the
        # FOR UPDATE player lock acquired above before this deploy's own final
        # commit — opening a race window where a concurrent deploy could slip
        # past the cap. Doing the recall in the same transaction keeps the lock
        # held continuously from the cap check through the single commit below.
        prior = await self.session.execute(
            select(DroneDeployment)
            .where(and_(
                DroneDeployment.drone_id == drone_id,
                DroneDeployment.is_active == True
            ))
        )
        prior_deployment = prior.scalar_one_or_none()
        if prior_deployment is not None:
            prior_deployment.is_active = False
            prior_deployment.recalled_at = datetime.utcnow()

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