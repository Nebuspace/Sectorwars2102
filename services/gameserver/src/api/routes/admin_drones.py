"""
Admin drone management endpoints.

Provides administrative control over all drones in the game.
"""

import json
from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from datetime import datetime

from src.core.database import get_async_session
from src.auth.dependencies import get_current_admin_user
from src.models.drone import Drone, DroneDeployment, DroneCombat
from src.models.user import User
from src.services.drone_service import DroneService


router = APIRouter(prefix="/admin/drones", tags=["admin-drones"])


class AdminDroneUpdate(BaseModel):
    """Admin drone update model."""
    name: Optional[str] = None
    level: Optional[int] = None
    health: Optional[int] = None
    max_health: Optional[int] = None
    attack_power: Optional[int] = None
    defense_power: Optional[int] = None
    speed: Optional[float] = None
    status: Optional[str] = None
    abilities: Optional[str] = None


class DroneStatistics(BaseModel):
    """Drone statistics model."""
    total_drones: int
    active_drones: int
    destroyed_drones: int
    deployed_drones: int
    in_combat_drones: int
    drones_by_type: dict
    average_level: float
    total_kills: int
    total_battles: int


@router.get("/", response_model=List)
async def get_all_drones(
    skip: int = 0,
    limit: int = Query(default=100, le=1000),
    player_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    sector_id: Optional[UUID] = None,
    drone_type: Optional[str] = None,
    status: Optional[str] = None,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Get all drones with optional filters."""
    query = select(Drone)
    
    if player_id:
        query = query.where(Drone.player_id == player_id)
    if team_id:
        query = query.where(Drone.team_id == team_id)
    if sector_id:
        query = query.where(Drone.sector_id == sector_id)
    if drone_type:
        query = query.where(Drone.drone_type == drone_type)
    if status:
        query = query.where(Drone.status == status)
        
    query = query.offset(skip).limit(limit)
    
    result = await db.execute(query)
    drones = result.scalars().all()
    
    # Convert to dict for JSON serialization
    return [
        {
            "id": str(drone.id),
            "player_id": str(drone.player_id),
            "team_id": str(drone.team_id) if drone.team_id else None,
            "drone_type": drone.drone_type,
            "name": drone.name,
            "level": drone.level,
            "health": drone.health,
            "max_health": drone.max_health,
            "attack_power": drone.attack_power,
            "defense_power": drone.defense_power,
            "speed": drone.speed,
            "status": drone.status,
            "sector_id": str(drone.sector_id) if drone.sector_id else None,
            "deployed_at": drone.deployed_at.isoformat() if drone.deployed_at else None,
            "last_action": drone.last_action.isoformat() if drone.last_action else None,
            "kills": drone.kills,
            "damage_dealt": drone.damage_dealt,
            "damage_taken": drone.damage_taken,
            "battles_fought": drone.battles_fought,
            "abilities": drone.abilities,
            "created_at": drone.created_at.isoformat(),
            "destroyed_at": drone.destroyed_at.isoformat() if drone.destroyed_at else None
        }
        for drone in drones
    ]


@router.get("/statistics", response_model=DroneStatistics)
async def get_drone_statistics(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Get overall drone statistics."""
    # Total drones
    total_result = await db.execute(select(func.count(Drone.id)))
    total_drones = total_result.scalar() or 0
    
    # Active drones (not destroyed)
    active_result = await db.execute(
        select(func.count(Drone.id))
        .where(Drone.status != "destroyed")
    )
    active_drones = active_result.scalar() or 0
    
    # Destroyed drones
    destroyed_drones = total_drones - active_drones
    
    # Deployed drones
    deployed_result = await db.execute(
        select(func.count(Drone.id))
        .where(Drone.status == "deployed")
    )
    deployed_drones = deployed_result.scalar() or 0
    
    # In combat drones
    combat_result = await db.execute(
        select(func.count(Drone.id))
        .where(Drone.status == "combat")
    )
    in_combat_drones = combat_result.scalar() or 0
    
    # Drones by type
    type_result = await db.execute(
        select(Drone.drone_type, func.count(Drone.id))
        .group_by(Drone.drone_type)
    )
    drones_by_type = {row[0]: row[1] for row in type_result}
    
    # Average level
    avg_level_result = await db.execute(
        select(func.avg(Drone.level))
        .where(Drone.status != "destroyed")
    )
    average_level = float(avg_level_result.scalar() or 1.0)
    
    # Total kills and battles
    stats_result = await db.execute(
        select(
            func.sum(Drone.kills),
            func.sum(Drone.battles_fought)
        )
    )
    stats_row = stats_result.first()
    total_kills = stats_row[0] or 0
    total_battles = stats_row[1] or 0
    
    return DroneStatistics(
        total_drones=total_drones,
        active_drones=active_drones,
        destroyed_drones=destroyed_drones,
        deployed_drones=deployed_drones,
        in_combat_drones=in_combat_drones,
        drones_by_type=drones_by_type,
        average_level=average_level,
        total_kills=total_kills,
        total_battles=total_battles
    )


def _parse_combat_log(raw: Optional[str]) -> Optional[list]:
    """Parse DroneCombat.combat_log (String(2000), can be hard-truncated by
    the writer) into a JSON list for the admin UI. Returns None on any
    parse failure so a truncated/malformed string degrades to "no round
    detail" rather than surfacing a raw, possibly-invalid JSON string."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


@router.get("/{drone_id}")
async def get_drone_details(
    drone_id: UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Get detailed information about a specific drone."""
    drone = await db.get(Drone, drone_id)
    
    if not drone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found"
        )
    
    # Get recent deployments
    deployments_result = await db.execute(
        select(DroneDeployment)
        .where(DroneDeployment.drone_id == drone_id)
        .order_by(DroneDeployment.deployed_at.desc())
        .limit(5)
    )
    deployments = deployments_result.scalars().all()
    
    # Get recent combats
    combats_result = await db.execute(
        select(DroneCombat)
        .where(
            (DroneCombat.attacker_drone_id == drone_id) |
            (DroneCombat.defender_drone_id == drone_id)
        )
        .order_by(DroneCombat.started_at.desc())
        .limit(5)
    )
    combats = combats_result.scalars().all()
    
    return {
        "drone": {
            "id": str(drone.id),
            "player_id": str(drone.player_id),
            "team_id": str(drone.team_id) if drone.team_id else None,
            "drone_type": drone.drone_type,
            "name": drone.name,
            "level": drone.level,
            "health": drone.health,
            "max_health": drone.max_health,
            "attack_power": drone.attack_power,
            "defense_power": drone.defense_power,
            "speed": drone.speed,
            "status": drone.status,
            "sector_id": str(drone.sector_id) if drone.sector_id else None,
            "deployed_at": drone.deployed_at.isoformat() if drone.deployed_at else None,
            "last_action": drone.last_action.isoformat() if drone.last_action else None,
            "kills": drone.kills,
            "damage_dealt": drone.damage_dealt,
            "damage_taken": drone.damage_taken,
            "battles_fought": drone.battles_fought,
            "abilities": drone.abilities,
            "created_at": drone.created_at.isoformat(),
            "destroyed_at": drone.destroyed_at.isoformat() if drone.destroyed_at else None
        },
        "recent_deployments": [
            {
                "id": str(d.id),
                "sector_id": str(d.sector_id),
                "deployed_at": d.deployed_at.isoformat(),
                "recalled_at": d.recalled_at.isoformat() if d.recalled_at else None,
                "is_active": d.is_active,
                "deployment_type": d.deployment_type,
                "enemies_destroyed": d.enemies_destroyed,
                "resources_collected": d.resources_collected,
                "damage_prevented": d.damage_prevented
            }
            for d in deployments
        ],
        "recent_combats": [
            {
                "id": str(c.id),
                "started_at": c.started_at.isoformat(),
                "ended_at": c.ended_at.isoformat() if c.ended_at else None,
                "rounds": c.rounds,
                "was_attacker": c.attacker_drone_id == drone_id,
                "won": c.winner_drone_id == drone_id,
                "damage_dealt": c.attacker_damage_dealt if c.attacker_drone_id == drone_id else c.defender_damage_dealt,
                "damage_taken": c.defender_damage_dealt if c.attacker_drone_id == drone_id else c.attacker_damage_dealt,
                "combat_log": _parse_combat_log(c.combat_log)
            }
            for c in combats
        ]
    }


@router.patch("/{drone_id}")
async def update_drone(
    drone_id: UUID,
    update: AdminDroneUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Update a drone's attributes."""
    drone = await db.get(Drone, drone_id)
    
    if not drone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found"
        )
    
    # Update fields
    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(drone, field, value)
    
    await db.commit()
    await db.refresh(drone)
    
    return {"message": "Drone updated successfully", "drone_id": str(drone_id)}


@router.delete("/{drone_id}")
async def delete_drone(
    drone_id: UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Permanently delete a drone."""
    drone = await db.get(Drone, drone_id)
    
    if not drone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found"
        )
    
    await db.delete(drone)
    await db.commit()
    
    return {"message": "Drone deleted successfully"}


@router.post("/{drone_id}/force-recall")
async def force_recall_drone(
    drone_id: UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Force recall a deployed drone."""
    service = DroneService(db)
    
    try:
        deployment = await service.recall_drone(drone_id)
        if not deployment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Drone is not deployed"
            )
        return {"message": "Drone forcefully recalled"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{drone_id}/restore")
async def restore_destroyed_drone(
    drone_id: UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Restore a destroyed drone to active status."""
    drone = await db.get(Drone, drone_id)
    
    if not drone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drone not found"
        )
    
    if drone.status != "destroyed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Drone is not destroyed"
        )
    
    # Restore drone
    drone.status = "deployed"
    drone.health = drone.max_health
    drone.destroyed_at = None
    
    await db.commit()
    
    return {"message": "Drone restored successfully"}


@router.get("/sector/{sector_id}/summary")
async def get_sector_drone_summary(
    sector_id: UUID,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_session)
):
    """Get a summary of drones in a specific sector."""
    # Get all drones in sector
    result = await db.execute(
        select(Drone)
        .where(Drone.sector_id == sector_id)
    )
    drones = result.scalars().all()
    
    # Group by player and team
    player_drones = {}
    team_drones = {}
    
    for drone in drones:
        # By player
        if drone.player_id not in player_drones:
            player_drones[str(drone.player_id)] = {
                "count": 0,
                "types": {},
                "total_power": 0
            }
        player_drones[str(drone.player_id)]["count"] += 1
        player_drones[str(drone.player_id)]["types"][drone.drone_type] = \
            player_drones[str(drone.player_id)]["types"].get(drone.drone_type, 0) + 1
        player_drones[str(drone.player_id)]["total_power"] += drone.attack_power + drone.defense_power
        
        # By team
        if drone.team_id:
            team_key = str(drone.team_id)
            if team_key not in team_drones:
                team_drones[team_key] = {
                    "count": 0,
                    "types": {},
                    "total_power": 0
                }
            team_drones[team_key]["count"] += 1
            team_drones[team_key]["types"][drone.drone_type] = \
                team_drones[team_key]["types"].get(drone.drone_type, 0) + 1
            team_drones[team_key]["total_power"] += drone.attack_power + drone.defense_power
    
    return {
        "sector_id": str(sector_id),
        "total_drones": len(drones),
        "by_player": player_drones,
        "by_team": team_drones,
        "drone_types": {
            drone_type: sum(1 for d in drones if d.drone_type == drone_type)
            for drone_type in set(d.drone_type for d in drones)
        }
    }