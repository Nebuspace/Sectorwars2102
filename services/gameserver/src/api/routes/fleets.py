"""
Fleet management API endpoints for players.

Handles fleet creation, management, and battle operations.

IMPORTANT: Named routes (e.g., /battles, /my-fleets) must be defined
BEFORE parameterized routes (e.g., /{fleet_id}) to avoid FastAPI
treating the named path segment as a path parameter.
"""

from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_async_session
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.fleet import FleetRole, FleetStatus
from src.services.fleet_service import FleetService

router = APIRouter(prefix="/fleets", tags=["fleets"])


# Request/Response Models

class CreateFleetRequest(BaseModel):
    """Request to create a new fleet."""
    name: str = Field(..., min_length=1, max_length=100)
    formation: str = Field(default="standard")
    commander_id: Optional[UUID] = None


class AddShipRequest(BaseModel):
    """Request to add a ship to a fleet."""
    ship_id: UUID
    role: str = Field(default=FleetRole.ATTACKER.value)


class FleetResponse(BaseModel):
    """Fleet response model."""
    id: UUID
    team_id: UUID
    name: str
    status: str
    formation: str
    total_ships: int
    total_firepower: int
    total_shields: int
    total_hull: int
    average_speed: float
    morale: int
    supply_level: int
    commander_id: Optional[UUID]
    commander_name: Optional[str]
    sector_id: Optional[UUID]
    sector_name: Optional[str]
    member_count: int

    class Config:
        from_attributes = True


class FleetMemberResponse(BaseModel):
    """Fleet member response model."""
    id: UUID
    ship_id: UUID
    ship_name: str
    ship_type: str
    player_id: UUID
    player_name: str
    role: str
    position: int
    ready_status: bool

    class Config:
        from_attributes = True


class BattleInitiateRequest(BaseModel):
    """Request to initiate a fleet battle."""
    defender_fleet_id: UUID


class BattleResponse(BaseModel):
    """Battle status response."""
    battle_id: UUID
    phase: str
    attacker_fleet_id: UUID
    attacker_fleet_name: str
    defender_fleet_id: UUID
    defender_fleet_name: str
    round: Optional[int]
    attacker_remaining: Optional[int]
    defender_remaining: Optional[int]
    battle_ongoing: bool
    winner: Optional[str]

    class Config:
        from_attributes = True


# Import required models
from src.models.fleet import Fleet, FleetBattle
from src.models.ship import Ship


# =============================================================================
# Collection-level endpoints (no path parameters)
# These MUST be defined before /{fleet_id} to prevent route conflicts
# =============================================================================

# Fleet Management Endpoints

@router.post("/", response_model=FleetResponse)
async def create_fleet(
    request: CreateFleetRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Create a new fleet for the player's team."""
    if not player.team_id:
        raise HTTPException(status_code=400, detail="Player must be in a team to create fleets")

    service = FleetService(db)

    try:
        fleet = service.create_fleet(
            team_id=player.team_id,
            name=request.name,
            commander_id=request.commander_id,
            formation=request.formation
        )

        return FleetResponse(
            id=fleet.id,
            team_id=fleet.team_id,
            name=fleet.name,
            status=fleet.status,
            formation=fleet.formation,
            total_ships=fleet.total_ships,
            total_firepower=fleet.total_firepower,
            total_shields=fleet.total_shields,
            total_hull=fleet.total_hull,
            average_speed=fleet.average_speed,
            morale=fleet.morale,
            supply_level=fleet.supply_level,
            commander_id=fleet.commander_id,
            commander_name=fleet.commander.username if fleet.commander else None,
            sector_id=fleet.sector_id,
            sector_name=fleet.sector.name if fleet.sector else None,
            member_count=len(fleet.members)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=List[FleetResponse])
async def get_team_fleets(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get all fleets for the player's team."""
    if not player.team_id:
        return []

    service = FleetService(db)
    fleets = service.get_team_fleets(player.team_id)

    return [
        FleetResponse(
            id=fleet.id,
            team_id=fleet.team_id,
            name=fleet.name,
            status=fleet.status,
            formation=fleet.formation,
            total_ships=fleet.total_ships,
            total_firepower=fleet.total_firepower,
            total_shields=fleet.total_shields,
            total_hull=fleet.total_hull,
            average_speed=fleet.average_speed,
            morale=fleet.morale,
            supply_level=fleet.supply_level,
            commander_id=fleet.commander_id,
            commander_name=fleet.commander.username if fleet.commander else None,
            sector_id=fleet.sector_id,
            sector_name=fleet.sector.name if fleet.sector else None,
            member_count=len(fleet.members)
        )
        for fleet in fleets
    ]


@router.get("/my-fleets", response_model=List[FleetResponse])
async def get_my_fleets(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get all fleets where the player has ships."""
    service = FleetService(db)
    fleets = service.get_player_fleets(player.id)

    return [
        FleetResponse(
            id=fleet.id,
            team_id=fleet.team_id,
            name=fleet.name,
            status=fleet.status,
            formation=fleet.formation,
            total_ships=fleet.total_ships,
            total_firepower=fleet.total_firepower,
            total_shields=fleet.total_shields,
            total_hull=fleet.total_hull,
            average_speed=fleet.average_speed,
            morale=fleet.morale,
            supply_level=fleet.supply_level,
            commander_id=fleet.commander_id,
            commander_name=fleet.commander.username if fleet.commander else None,
            sector_id=fleet.sector_id,
            sector_name=fleet.sector.name if fleet.sector else None,
            member_count=len(fleet.members)
        )
        for fleet in fleets
    ]


# Fleet Battle Endpoints (named routes before /{fleet_id})

@router.get("/battles", response_model=List[BattleResponse])
async def get_team_battles(
    active_only: bool = Query(False),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get all battles involving the player's team."""
    if not player.team_id:
        return []

    service = FleetService(db)
    battles = service.get_fleet_battles(team_id=player.team_id, active_only=active_only)

    return [
        BattleResponse(
            battle_id=battle.id,
            phase=battle.phase,
            attacker_fleet_id=battle.attacker_fleet_id,
            attacker_fleet_name=battle.attacker_fleet.name if battle.attacker_fleet else "Unknown",
            defender_fleet_id=battle.defender_fleet_id,
            defender_fleet_name=battle.defender_fleet.name if battle.defender_fleet else "Unknown",
            round=len(battle.battle_log),
            attacker_remaining=battle.attacker_fleet.total_ships if battle.attacker_fleet else 0,
            defender_remaining=battle.defender_fleet.total_ships if battle.defender_fleet else 0,
            battle_ongoing=battle.ended_at is None,
            winner=battle.winner
        )
        for battle in battles
    ]


@router.post("/battles/{battle_id}/simulate-round")
async def simulate_battle_round(
    battle_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Simulate one round of fleet battle."""
    # Verify player is involved in the battle
    battle = db.query(FleetBattle).filter(FleetBattle.id == battle_id).first()
    if not battle:
        raise HTTPException(status_code=404, detail="Battle not found")

    # Check if player has ships in either fleet (not just team membership)
    from src.models.fleet import FleetMember
    attacker = battle.attacker_fleet
    defender = battle.defender_fleet

    player_in_attacker = attacker and db.query(FleetMember).filter(
        FleetMember.fleet_id == attacker.id,
        FleetMember.player_id == player.id
    ).first() is not None

    player_in_defender = defender and db.query(FleetMember).filter(
        FleetMember.fleet_id == defender.id,
        FleetMember.player_id == player.id
    ).first() is not None

    if not (player_in_attacker or player_in_defender):
        raise HTTPException(status_code=403, detail="You have no ships in this battle")

    service = FleetService(db)

    try:
        result = service.simulate_battle_round(battle_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Parameterized routes - /{fleet_id} and sub-routes
# These MUST come after all named routes above
# =============================================================================

@router.get("/{fleet_id}", response_model=FleetResponse)
async def get_fleet(
    fleet_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get details of a specific fleet."""
    service = FleetService(db)

    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Check if player can view this fleet
    if fleet.team_id != player.team_id:
        # Check if player has ships in this fleet
        player_fleets = service.get_player_fleets(player.id)
        if fleet not in player_fleets:
            raise HTTPException(status_code=403, detail="Cannot view this fleet")

    return FleetResponse(
        id=fleet.id,
        team_id=fleet.team_id,
        name=fleet.name,
        status=fleet.status,
        formation=fleet.formation,
        total_ships=fleet.total_ships,
        total_firepower=fleet.total_firepower,
        total_shields=fleet.total_shields,
        total_hull=fleet.total_hull,
        average_speed=fleet.average_speed,
        morale=fleet.morale,
        supply_level=fleet.supply_level,
        commander_id=fleet.commander_id,
        commander_name=fleet.commander.username if fleet.commander else None,
        sector_id=fleet.sector_id,
        sector_name=fleet.sector.name if fleet.sector else None,
        member_count=len(fleet.members)
    )


@router.get("/{fleet_id}/members", response_model=List[FleetMemberResponse])
async def get_fleet_members(
    fleet_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get all members of a fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Check permissions
    if fleet.team_id != player.team_id:
        service = FleetService(db)
        player_fleets = service.get_player_fleets(player.id)
        if fleet not in player_fleets:
            raise HTTPException(status_code=403, detail="Cannot view this fleet")

    return [
        FleetMemberResponse(
            id=member.id,
            ship_id=member.ship_id,
            ship_name=member.ship.name if member.ship else "Unknown",
            ship_type=member.ship.type if member.ship else "Unknown",
            player_id=member.player_id,
            player_name=member.player.username if member.player else "Unknown",
            role=member.role,
            position=member.position,
            ready_status=member.ready_status
        )
        for member in fleet.members
    ]


@router.post("/{fleet_id}/add-ship", response_model=FleetMemberResponse)
async def add_ship_to_fleet(
    fleet_id: UUID,
    request: AddShipRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Add a ship to a fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    if fleet.team_id != player.team_id:
        raise HTTPException(status_code=403, detail="Cannot modify this fleet")

    # Verify ship ownership
    ship = db.query(Ship).filter(Ship.id == request.ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    if ship.player_id != player.id:
        raise HTTPException(status_code=403, detail="You don't own this ship")

    service = FleetService(db)

    try:
        # Convert string role to enum
        role = FleetRole(request.role)
        member = service.add_ship_to_fleet(fleet_id, request.ship_id, role)

        return FleetMemberResponse(
            id=member.id,
            ship_id=member.ship_id,
            ship_name=member.ship.name,
            ship_type=member.ship.type,
            player_id=member.player_id,
            player_name=member.player.username,
            role=member.role,
            position=member.position,
            ready_status=member.ready_status
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{fleet_id}/remove-ship/{ship_id}")
async def remove_ship_from_fleet(
    fleet_id: UUID,
    ship_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Remove a ship from a fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Check permissions - team member or ship owner
    ship = db.query(Ship).filter(Ship.id == ship_id).first()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    if fleet.team_id != player.team_id and ship.player_id != player.id:
        raise HTTPException(status_code=403, detail="Cannot remove this ship")

    service = FleetService(db)
    success = service.remove_ship_from_fleet(fleet_id, ship_id)

    if not success:
        raise HTTPException(status_code=400, detail="Ship not in fleet")

    return {"message": "Ship removed from fleet"}


@router.patch("/{fleet_id}/formation")
async def update_fleet_formation(
    fleet_id: UUID,
    formation: str = Query(..., pattern="^(standard|aggressive|defensive|flanking|turtle)$"),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Update fleet formation."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Only commander or team leader can change formation
    if fleet.commander_id != player.id:
        if fleet.team_id != player.team_id or not player.team.leader_id == player.id:
            raise HTTPException(status_code=403, detail="Only fleet commander can change formation")

    service = FleetService(db)
    fleet = service.set_fleet_formation(fleet_id, formation)

    return {"message": f"Formation changed to {formation}"}


@router.patch("/{fleet_id}/commander")
async def update_fleet_commander(
    fleet_id: UUID,
    commander_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Assign a new fleet commander."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Only team leader can change commander
    if fleet.team_id != player.team_id or player.team.leader_id != player.id:
        raise HTTPException(status_code=403, detail="Only team leader can assign commanders")

    service = FleetService(db)

    try:
        fleet = service.set_fleet_commander(fleet_id, commander_id)
        return {"message": "Commander updated"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{fleet_id}")
async def disband_fleet(
    fleet_id: UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Disband a fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Only commander or team leader can disband
    if fleet.commander_id != player.id:
        if fleet.team_id != player.team_id or player.team.leader_id != player.id:
            raise HTTPException(status_code=403, detail="Cannot disband this fleet")

    service = FleetService(db)

    try:
        success = service.disband_fleet(fleet_id)
        if success:
            return {"message": "Fleet disbanded"}
        else:
            raise HTTPException(status_code=400, detail="Failed to disband fleet")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{fleet_id}/initiate-battle", response_model=BattleResponse)
async def initiate_battle(
    fleet_id: UUID,
    request: BattleInitiateRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Initiate a battle with another fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    # Only commander can initiate battles
    if fleet.commander_id != player.id:
        raise HTTPException(status_code=403, detail="Only fleet commander can initiate battles")

    service = FleetService(db)

    try:
        battle = service.initiate_battle(fleet_id, request.defender_fleet_id)

        return BattleResponse(
            battle_id=battle.id,
            phase=battle.phase,
            attacker_fleet_id=battle.attacker_fleet_id,
            attacker_fleet_name=battle.attacker_fleet.name if battle.attacker_fleet else "Unknown",
            defender_fleet_id=battle.defender_fleet_id,
            defender_fleet_name=battle.defender_fleet.name if battle.defender_fleet else "Unknown",
            round=1,
            attacker_remaining=battle.attacker_ships_initial,
            defender_remaining=battle.defender_ships_initial,
            battle_ongoing=True,
            winner=None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
