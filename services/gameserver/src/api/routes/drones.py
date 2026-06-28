"""
Drone management API endpoints.

Provides endpoints for creating, deploying, and managing drones.
"""

import logging
from uuid import UUID, uuid4
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime

logger = logging.getLogger(__name__)

from src.core.database import get_async_session
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.drone import Drone, DroneDeployment, DroneType, DroneStatus
from src.services.drone_service import DroneService


router = APIRouter(prefix="/drones", tags=["drones"])


# Request/Response models
class CreateDroneRequest(BaseModel):
    """Request to create a new drone."""
    drone_type: str
    name: Optional[str] = None
    team_id: Optional[UUID] = None


class DeployDroneRequest(BaseModel):
    """Request to deploy a drone."""
    sector_id: UUID
    deployment_type: str = "defense"
    target_id: Optional[UUID] = None


class DeployDronesRequest(BaseModel):
    """Request to deploy multiple drones (API contract version)."""
    sectorId: str
    droneCount: int


class RepairDroneRequest(BaseModel):
    """Request to repair a drone."""
    repair_amount: int


class DroneResponse(BaseModel):
    """Response model for drone data."""
    id: UUID
    player_id: UUID
    team_id: Optional[UUID]
    drone_type: str
    name: Optional[str]
    level: int
    health: int
    max_health: int
    attack_power: int
    defense_power: int
    speed: float
    status: Optional[str]
    sector_id: Optional[UUID]
    deployed_at: Optional[datetime]
    last_action: Optional[datetime]
    kills: int
    damage_dealt: int
    damage_taken: int
    battles_fought: int
    abilities: Optional[str]
    created_at: datetime
    destroyed_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class DroneDeploymentResponse(BaseModel):
    """Response model for drone deployment data."""
    id: UUID
    drone_id: UUID
    player_id: UUID
    sector_id: UUID
    deployed_at: datetime
    recalled_at: Optional[datetime]
    is_active: bool
    deployment_type: str
    target_id: Optional[UUID]
    enemies_destroyed: int
    resources_collected: int
    damage_prevented: int
    
    class Config:
        from_attributes = True


@router.post("/", response_model=DroneResponse)
async def create_drone(
    request: CreateDroneRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Create a new drone for the current player."""
    service = DroneService(db)
    
    try:
        drone = await service.create_drone(
            player_id=current_player.id,
            drone_type=request.drone_type,
            name=request.name,
            team_id=request.team_id
        )
        return drone
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/", response_model=List[DroneResponse])
async def get_my_drones(
    include_destroyed: bool = False,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all drones owned by the current player."""
    service = DroneService(db)
    drones = await service.get_player_drones(
        player_id=current_player.id,
        include_destroyed=include_destroyed
    )
    return drones


@router.get("/types")
async def get_drone_types():
    """Get available drone types and their characteristics."""
    return {
        "types": [
            {
                "type": DroneType.ATTACK.value,
                "description": "High damage output, fast movement",
                "base_stats": {
                    "health": 80,
                    "attack_power": 20,
                    "defense_power": 5,
                    "speed": 1.5
                },
                "abilities": ["precision_strike", "rapid_fire"]
            },
            {
                "type": DroneType.DEFENSE.value,
                "description": "High health and defense, area protection",
                "base_stats": {
                    "health": 150,
                    "attack_power": 8,
                    "defense_power": 20,
                    "speed": 0.8
                },
                "abilities": ["shield_boost", "area_defense"]
            },
            {
                "type": DroneType.SCOUT.value,
                "description": "Fast movement, enhanced sensors",
                "base_stats": {
                    "health": 60,
                    "attack_power": 5,
                    "defense_power": 8,
                    "speed": 2.0
                },
                "abilities": ["enhanced_sensors", "stealth"]
            },
            {
                "type": DroneType.MINING.value,
                "description": "Resource extraction, cargo capacity",
                "base_stats": {
                    "health": 100,
                    "attack_power": 3,
                    "defense_power": 10,
                    "speed": 1.0
                },
                "abilities": ["resource_extraction", "cargo_boost"]
            },
            {
                "type": DroneType.REPAIR.value,
                "description": "Repair and support abilities",
                "base_stats": {
                    "health": 90,
                    "attack_power": 2,
                    "defense_power": 12,
                    "speed": 1.2
                },
                "abilities": ["repair_beam", "shield_recharge"]
            }
        ]
    }


@router.get("/{drone_id}", response_model=DroneResponse)
async def get_drone(
    drone_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get a specific drone by ID."""
    drone = await db.get(Drone, drone_id)
    
    if not drone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found"
        )
        
    # Check ownership
    if drone.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't own this drone"
        )
        
    return drone


@router.post("/{drone_id}/deploy", response_model=DroneDeploymentResponse)
async def deploy_drone(
    drone_id: UUID,
    request: DeployDroneRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Deploy a drone to a sector."""
    # Verify drone ownership
    drone = await db.get(Drone, drone_id)
    if not drone or drone.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found or not owned by you"
        )
        
    service = DroneService(db)
    
    try:
        deployment = await service.deploy_drone(
            drone_id=drone_id,
            sector_id=request.sector_id,
            deployment_type=request.deployment_type,
            target_id=request.target_id
        )
        return deployment
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{drone_id}/recall")
async def recall_drone(
    drone_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Recall a deployed drone."""
    # Verify drone ownership
    drone = await db.get(Drone, drone_id)
    if not drone or drone.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found or not owned by you"
        )
        
    service = DroneService(db)
    deployment = await service.recall_drone(drone_id)
    
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Drone is not deployed"
        )
        
    return {"message": "Drone recalled successfully"}


@router.post("/{drone_id}/repair", response_model=DroneResponse)
async def repair_drone(
    drone_id: UUID,
    request: RepairDroneRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Repair a damaged drone."""
    # Verify drone ownership
    drone = await db.get(Drone, drone_id)
    if not drone or drone.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found or not owned by you"
        )
        
    service = DroneService(db)
    
    try:
        drone = await service.repair_drone(drone_id, request.repair_amount)
        return drone
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{drone_id}/upgrade", response_model=DroneResponse)
async def upgrade_drone(
    drone_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Upgrade a drone to the next level."""
    # Verify drone ownership
    drone = await db.get(Drone, drone_id)
    if not drone or drone.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found or not owned by you"
        )
        
    service = DroneService(db)
    
    try:
        drone = await service.upgrade_drone(drone_id)
        return drone
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/deployments", response_model=List[DroneDeploymentResponse])
async def get_my_deployments(
    active_only: bool = True,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all drone deployments for the current player."""
    service = DroneService(db)
    deployments = await service.get_drone_deployments(
        player_id=current_player.id,
        active_only=active_only
    )
    return deployments


@router.get("/sector/{sector_id}", response_model=List[DroneResponse])
async def get_sector_drones(
    sector_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all active drones in a sector.

    Requires authentication: sector drone presence is tactical intelligence
    (it reveals players' military deployments and positions) and must not be
    enumerable by anonymous callers. Matches the auth posture of the sibling
    drone read endpoints (team/{id}).
    """
    service = DroneService(db)
    drones = await service.get_sector_drones(sector_id)
    return drones


@router.get("/team/{team_id}", response_model=List[DroneResponse])
async def get_team_drones(
    team_id: UUID,
    include_destroyed: bool = False,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all drones assigned to a team."""
    # Verify player is a member of the team
    if not current_player.team_id or str(current_player.team_id) != str(team_id):
        raise HTTPException(status_code=403, detail="You are not a member of this team")

    service = DroneService(db)
    drones = await service.get_team_drones(
        team_id=team_id,
        include_destroyed=include_destroyed
    )
    return drones


# API Contract compliant endpoints for Player UI

@router.post("/deploy")
async def deploy_drones_contract(
    request: DeployDronesRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Deploy multiple drones to a sector (API contract version)."""
    try:
        sector_id = UUID(request.sectorId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sector ID format"
        )
    
    # Get player's available drones
    service = DroneService(db)
    available_drones = await service.get_player_drones(
        player_id=current_player.id,
        include_destroyed=False
    )
    
    # Filter for drones not currently deployed
    undeployed_drones = [d for d in available_drones if d.status != DroneStatus.DEPLOYED.value]
    
    if len(undeployed_drones) < request.droneCount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough available drones. Have {len(undeployed_drones)}, requested {request.droneCount}"
        )
    
    # Deploy the requested number of drones, collecting the REAL deployment-row
    # ids. The previous version returned a random uuid4 unrelated to any row, so
    # DELETE /{deploymentId}/recall (which does db.get(DroneDeployment, id))
    # always 404'd. Each deploy_drone creates one DroneDeployment; return its id.
    deployed_count = 0
    deployment_ids: list[str] = []
    last_error: Optional[str] = None

    for i in range(min(request.droneCount, len(undeployed_drones))):
        drone = undeployed_drones[i]
        try:
            deployment = await service.deploy_drone(
                drone_id=drone.id,
                sector_id=sector_id,
                deployment_type="defense"
            )
            deployed_count += 1
            if deployment is not None:
                deployment_ids.append(str(deployment.id))
        except ValueError as e:
            # Per-drone reject (e.g. the per-ship drone cap was reached). Stop
            # the batch — the cap is monotonic for this batch, so once one deploy
            # is capped, every remaining one will be too. Within-cap deploys
            # already done above are kept (clamp behaviour).
            last_error = str(e)
            logger.info(f"Stopping batch deploy at drone {drone.id}: {e}")
            break
        except Exception as e:
            # Continue deploying others even if one fails for an unexpected reason
            last_error = str(e)
            logger.warning(f"Failed to deploy drone {drone.id}: {e}")

    if deployed_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=last_error or "No drones could be deployed",
        )

    return {
        # First real deployment id for the single-id contract; deploymentIds
        # lists every row created so the caller can recall each.
        "deploymentId": deployment_ids[0] if deployment_ids else None,
        "deploymentIds": deployment_ids,
        "dronesDeployed": deployed_count
    }


@router.get("/deployed")
async def get_deployed_drones_contract(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all deployed drones (API contract version)."""
    service = DroneService(db)
    deployments = await service.get_drone_deployments(
        player_id=current_player.id,
        active_only=True
    )
    
    # Transform to API contract format
    result = []
    for deployment in deployments:
        result.append({
            "deploymentId": str(deployment.id),
            "droneId": str(deployment.drone_id),
            "sectorId": str(deployment.sector_id),
            "deployedAt": deployment.deployed_at.isoformat(),
            "droneType": deployment.drone.drone_type if deployment.drone else "unknown",
            "health": deployment.drone.health if deployment.drone else 0,
            "maxHealth": deployment.drone.max_health if deployment.drone else 0
        })
    
    return {"deployments": result}


@router.delete("/{deploymentId}/recall")
async def recall_drones_contract(
    deploymentId: str,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session)
):
    """Recall deployed drones (API contract version)."""
    try:
        deployment_id = UUID(deploymentId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid deployment ID format"
        )
    
    # Get the deployment
    deployment = await db.get(DroneDeployment, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment not found"
        )
    
    # Verify ownership
    if deployment.player_id != current_player.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't own this deployment"
        )
    
    service = DroneService(db)
    await service.recall_drone(deployment.drone_id)
    
    return {"dronesRecalled": 1}