"""
Admin fleet management API endpoints.

Provides administrative controls for fleet operations, battles,
and monitoring across the entire game.

IMPORTANT: Named routes (e.g., /battles, /stats) must be defined
BEFORE parameterized routes (e.g., /{fleet_id}) to avoid FastAPI
treating the named path segment as a path parameter.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import get_current_user, require_scope
from src.models.user import User
from src.models.fleet import Fleet, FleetBattle, FleetMember, FleetBattleCasualty, FleetStatus
from src.models.team import Team
from src.models.player import Player
from src.services.fleet_service import FleetService
from src.services.audit_service import AuditService, AuditAction

router = APIRouter(prefix="/admin/fleets", tags=["admin", "fleets"])


# Response Models

class AdminFleetResponse(BaseModel):
    """Detailed fleet information for admin view."""
    id: UUID
    team_id: UUID
    team_name: str
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
    created_at: datetime
    last_battle: Optional[datetime]

    class Config:
        from_attributes = True


class AdminBattleResponse(BaseModel):
    """Detailed battle information for admin view."""
    id: UUID
    phase: str
    started_at: datetime
    ended_at: Optional[datetime]
    attacker_fleet_id: Optional[UUID]
    attacker_fleet_name: Optional[str]
    attacker_team_name: Optional[str]
    defender_fleet_id: Optional[UUID]
    defender_fleet_name: Optional[str]
    defender_team_name: Optional[str]
    sector_id: Optional[UUID]
    sector_name: Optional[str]
    attacker_ships_initial: int
    defender_ships_initial: int
    attacker_ships_destroyed: int
    defender_ships_destroyed: int
    attacker_ships_retreated: int
    defender_ships_retreated: int
    total_damage_dealt: int
    winner: Optional[str]
    credits_looted: int
    duration: Optional[str]

    class Config:
        from_attributes = True


class FleetStatsResponse(BaseModel):
    """Fleet statistics summary."""
    total_fleets: int
    active_fleets: int
    fleets_in_battle: int
    total_ships_in_fleets: int
    total_firepower: int
    average_fleet_size: float
    battles_today: int
    battles_this_week: int
    most_powerful_fleet: Optional[Dict[str, Any]]
    largest_fleet: Optional[Dict[str, Any]]


class ForceDissolveRequest(BaseModel):
    """Request to force dissolve a fleet."""
    reason: str = Field(..., min_length=10, max_length=500)


class InterveneBattleRequest(BaseModel):
    """Request to intervene in a battle."""
    action: str = Field(..., pattern="^(end_battle|pause_battle|force_winner)$")
    winner: Optional[str] = Field(None, pattern="^(attacker|defender|draw)$")
    reason: str = Field(..., min_length=10, max_length=500)


# =============================================================================
# Collection-level endpoints (no path parameters)
# These MUST be defined before /{fleet_id} to prevent route conflicts
# =============================================================================

# Fleet Management Endpoints

@router.get("/", response_model=List[AdminFleetResponse])
async def get_all_fleets(
    status: Optional[str] = Query(None),
    team_id: Optional[UUID] = Query(None),
    sector_id: Optional[UUID] = Query(None),
    in_battle: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all fleets with optional filters."""
    query = db.query(Fleet)

    if status:
        try:
            validated_status = FleetStatus(status).value
        except ValueError:
            valid_values = [s.value for s in FleetStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid fleet status '{status}'. Valid values: {valid_values}"
            )
        query = query.filter(Fleet.status == validated_status)
    if team_id:
        query = query.filter(Fleet.team_id == team_id)
    if sector_id:
        query = query.filter(Fleet.sector_id == sector_id)
    if in_battle is not None:
        if in_battle:
            query = query.filter(Fleet.status == FleetStatus.IN_BATTLE.value)
        else:
            query = query.filter(Fleet.status != FleetStatus.IN_BATTLE.value)

    fleets = query.offset(skip).limit(limit).all()

    return [
        AdminFleetResponse(
            id=fleet.id,
            team_id=fleet.team_id,
            team_name=fleet.team.name if fleet.team else "Unknown",
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
            commander_name=fleet.commander.name if fleet.commander else None,
            sector_id=fleet.sector_id,
            sector_name=fleet.sector.name if fleet.sector else None,
            member_count=len(fleet.members),
            created_at=fleet.created_at,
            last_battle=fleet.last_battle
        )
        for fleet in fleets
    ]


@router.get("/stats", response_model=FleetStatsResponse)
async def get_fleet_statistics(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get fleet statistics summary."""
    # Total fleets
    total_fleets = db.query(func.count(Fleet.id)).scalar()

    # Active fleets (not disbanded)
    active_fleets = db.query(func.count(Fleet.id)).filter(
        Fleet.status != FleetStatus.DISBANDED.value
    ).scalar()

    # Fleets in battle
    fleets_in_battle = db.query(func.count(Fleet.id)).filter(
        Fleet.status == FleetStatus.IN_BATTLE.value
    ).scalar()

    # Total ships in fleets
    total_ships = db.query(func.sum(Fleet.total_ships)).filter(
        Fleet.status != FleetStatus.DISBANDED.value
    ).scalar() or 0

    # Total firepower
    total_firepower = db.query(func.sum(Fleet.total_firepower)).filter(
        Fleet.status != FleetStatus.DISBANDED.value
    ).scalar() or 0

    # Average fleet size
    avg_size = total_ships / active_fleets if active_fleets > 0 else 0

    # Battles today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    battles_today = db.query(func.count(FleetBattle.id)).filter(
        FleetBattle.started_at >= today_start
    ).scalar()

    # Battles this week
    week_start = today_start.replace(day=today_start.day - today_start.weekday())
    battles_week = db.query(func.count(FleetBattle.id)).filter(
        FleetBattle.started_at >= week_start
    ).scalar()

    # Most powerful fleet
    powerful_fleet = db.query(Fleet).filter(
        Fleet.status != FleetStatus.DISBANDED.value
    ).order_by(Fleet.total_firepower.desc()).first()

    # Largest fleet
    largest_fleet = db.query(Fleet).filter(
        Fleet.status != FleetStatus.DISBANDED.value
    ).order_by(Fleet.total_ships.desc()).first()

    return FleetStatsResponse(
        total_fleets=total_fleets,
        active_fleets=active_fleets,
        fleets_in_battle=fleets_in_battle,
        total_ships_in_fleets=total_ships,
        total_firepower=total_firepower,
        average_fleet_size=round(avg_size, 1),
        battles_today=battles_today,
        battles_this_week=battles_week,
        most_powerful_fleet={
            "id": str(powerful_fleet.id),
            "name": powerful_fleet.name,
            "team": powerful_fleet.team.name,
            "firepower": powerful_fleet.total_firepower
        } if powerful_fleet else None,
        largest_fleet={
            "id": str(largest_fleet.id),
            "name": largest_fleet.name,
            "team": largest_fleet.team.name,
            "ships": largest_fleet.total_ships
        } if largest_fleet else None
    )


# Battle Management Endpoints (named routes before /{fleet_id})

@router.get("/battles", response_model=List[AdminBattleResponse])
async def get_all_battles(
    active_only: bool = Query(False),
    team_id: Optional[UUID] = Query(None),
    sector_id: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all fleet battles with optional filters."""
    query = db.query(FleetBattle)

    if active_only:
        query = query.filter(FleetBattle.ended_at.is_(None))
    if sector_id:
        query = query.filter(FleetBattle.sector_id == sector_id)

    if team_id:
        # Filter by team involvement
        query = query.join(
            Fleet,
            or_(
                Fleet.id == FleetBattle.attacker_fleet_id,
                Fleet.id == FleetBattle.defender_fleet_id
            )
        ).filter(Fleet.team_id == team_id)

    battles = query.order_by(FleetBattle.started_at.desc()).offset(skip).limit(limit).all()

    results = []
    for battle in battles:
        duration = None
        if battle.ended_at:
            delta = battle.ended_at - battle.started_at
            duration = str(delta)

        results.append(AdminBattleResponse(
            id=battle.id,
            phase=battle.phase,
            started_at=battle.started_at,
            ended_at=battle.ended_at,
            attacker_fleet_id=battle.attacker_fleet_id,
            attacker_fleet_name=battle.attacker_fleet.name if battle.attacker_fleet else None,
            attacker_team_name=battle.attacker_fleet.team.name if battle.attacker_fleet and battle.attacker_fleet.team else None,
            defender_fleet_id=battle.defender_fleet_id,
            defender_fleet_name=battle.defender_fleet.name if battle.defender_fleet else None,
            defender_team_name=battle.defender_fleet.team.name if battle.defender_fleet and battle.defender_fleet.team else None,
            sector_id=battle.sector_id,
            sector_name=battle.sector.name if battle.sector else None,
            attacker_ships_initial=battle.attacker_ships_initial,
            defender_ships_initial=battle.defender_ships_initial,
            attacker_ships_destroyed=battle.attacker_ships_destroyed,
            defender_ships_destroyed=battle.defender_ships_destroyed,
            attacker_ships_retreated=battle.attacker_ships_retreated,
            defender_ships_retreated=battle.defender_ships_retreated,
            total_damage_dealt=battle.total_damage_dealt,
            winner=battle.winner,
            credits_looted=battle.credits_looted,
            duration=duration
        ))

    return results


@router.get("/battles/{battle_id}")
async def get_battle_details(
    battle_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific battle."""
    battle = db.query(FleetBattle).filter(FleetBattle.id == battle_id).first()
    if not battle:
        raise HTTPException(status_code=404, detail="Battle not found")

    # Get casualty information
    casualties = db.query(FleetBattleCasualty).filter(
        FleetBattleCasualty.battle_id == battle_id
    ).all()

    casualty_summary = {
        "attacker_casualties": [],
        "defender_casualties": []
    }

    for casualty in casualties:
        casualty_data = {
            "ship_name": casualty.ship_name,
            "ship_type": casualty.ship_type,
            "player_name": casualty.player.name if casualty.player else "Unknown",
            "destroyed": casualty.destroyed,
            "retreated": casualty.retreated,
            "damage_taken": casualty.damage_taken,
            "damage_dealt": casualty.damage_dealt,
            "kills": casualty.kills
        }

        if casualty.was_attacker:
            casualty_summary["attacker_casualties"].append(casualty_data)
        else:
            casualty_summary["defender_casualties"].append(casualty_data)

    return {
        "battle": AdminBattleResponse(
            id=battle.id,
            phase=battle.phase,
            started_at=battle.started_at,
            ended_at=battle.ended_at,
            attacker_fleet_id=battle.attacker_fleet_id,
            attacker_fleet_name=battle.attacker_fleet.name if battle.attacker_fleet else None,
            attacker_team_name=battle.attacker_fleet.team.name if battle.attacker_fleet and battle.attacker_fleet.team else None,
            defender_fleet_id=battle.defender_fleet_id,
            defender_fleet_name=battle.defender_fleet.name if battle.defender_fleet else None,
            defender_team_name=battle.defender_fleet.team.name if battle.defender_fleet and battle.defender_fleet.team else None,
            sector_id=battle.sector_id,
            sector_name=battle.sector.name if battle.sector else None,
            attacker_ships_initial=battle.attacker_ships_initial,
            defender_ships_initial=battle.defender_ships_initial,
            attacker_ships_destroyed=battle.attacker_ships_destroyed,
            defender_ships_destroyed=battle.defender_ships_destroyed,
            attacker_ships_retreated=battle.attacker_ships_retreated,
            defender_ships_retreated=battle.defender_ships_retreated,
            total_damage_dealt=battle.total_damage_dealt,
            winner=battle.winner,
            credits_looted=battle.credits_looted,
            duration=str(battle.ended_at - battle.started_at) if battle.ended_at else None
        ),
        "casualties": casualty_summary,
        "battle_log": battle.battle_log
    }


@router.post("/battles/{battle_id}/intervene")
async def intervene_in_battle(
    battle_id: UUID,
    request: InterveneBattleRequest,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Intervene in an ongoing battle."""
    battle = db.query(FleetBattle).filter(FleetBattle.id == battle_id).first()
    if not battle:
        raise HTTPException(status_code=404, detail="Battle not found")

    if battle.ended_at:
        raise HTTPException(status_code=400, detail="Battle has already ended")

    service = FleetService(db)

    # Log intervention
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.UPDATE,
        resource_type="fleet_battle",
        resource_id=str(battle_id),
        details={
            "action": request.action,
            "winner": request.winner,
            "reason": request.reason
        }
    )

    if request.action == "end_battle":
        # End battle immediately
        if request.winner:
            battle.winner = request.winner
        result = service._end_battle(battle)
        return {"message": "Battle ended", "result": result}

    elif request.action == "pause_battle":
        # Pause battle by setting fleets to ready
        if battle.attacker_fleet:
            battle.attacker_fleet.status = FleetStatus.READY.value
        if battle.defender_fleet:
            battle.defender_fleet.status = FleetStatus.READY.value
        db.commit()
        return {"message": "Battle paused"}

    elif request.action == "force_winner":
        if not request.winner:
            raise HTTPException(status_code=400, detail="Winner must be specified")
        battle.winner = request.winner
        result = service._end_battle(battle)
        return {"message": f"Battle ended with {request.winner} as winner", "result": result}

    return {"message": "Intervention completed"}


# =============================================================================
# Parameterized routes - /{fleet_id} and sub-routes
# These MUST come after all named routes above
# =============================================================================

@router.get("/{fleet_id}", response_model=AdminFleetResponse)
async def get_fleet_details(
    fleet_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific fleet."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    return AdminFleetResponse(
        id=fleet.id,
        team_id=fleet.team_id,
        team_name=fleet.team.name if fleet.team else "Unknown",
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
        commander_name=fleet.commander.name if fleet.commander else None,
        sector_id=fleet.sector_id,
        sector_name=fleet.sector.name if fleet.sector else None,
        member_count=len(fleet.members),
        created_at=fleet.created_at,
        last_battle=fleet.last_battle
    )


@router.get("/{fleet_id}/members")
async def get_fleet_members(
    fleet_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all members of a fleet with detailed ship information."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    members = []
    for member in fleet.members:
        ship = member.ship
        members.append({
            "member_id": str(member.id),
            "ship_id": str(member.ship_id),
            "ship_name": ship.name if ship else "Unknown",
            "ship_type": ship.type if ship else "Unknown",
            "player_id": str(member.player_id),
            "player_name": member.player.name if member.player else "Unknown",
            "role": member.role,
            "position": member.position,
            "ready_status": member.ready_status,
            "ship_health": {
                "armor": ship.armor if ship else 0,
                "max_armor": ship.max_armor if ship else 0,
                "shields": ship.shields if ship else 0,
                "max_shields": ship.max_shields if ship else 0
            } if ship else None
        })

    return {"fleet_id": str(fleet_id), "members": members}


@router.patch("/{fleet_id}/morale")
async def adjust_fleet_morale(
    fleet_id: UUID,
    morale: int = Query(..., ge=0, le=100),
    reason: str = Query(..., min_length=10),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Adjust fleet morale administratively."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    old_morale = fleet.morale
    fleet.morale = morale

    # Log action
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.UPDATE,
        resource_type="fleet",
        resource_id=str(fleet_id),
        details={
            "field": "morale",
            "old_value": old_morale,
            "new_value": morale,
            "reason": reason
        }
    )

    db.commit()

    return {"message": f"Fleet morale adjusted from {old_morale} to {morale}"}


@router.delete("/{fleet_id}/force-dissolve")
async def force_dissolve_fleet(
    fleet_id: UUID,
    request: ForceDissolveRequest,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Force dissolve a fleet administratively."""
    fleet = db.query(Fleet).filter(Fleet.id == fleet_id).first()
    if not fleet:
        raise HTTPException(status_code=404, detail="Fleet not found")

    service = FleetService(db)

    # Log action
    audit_service = AuditService(db)
    audit_service.log_action(
        user_id=admin.id,
        action=AuditAction.DELETE,
        resource_type="fleet",
        resource_id=str(fleet_id),
        details={
            "fleet_name": fleet.name,
            "team": fleet.team.name if fleet.team else "Unknown",
            "ships": fleet.total_ships,
            "reason": request.reason
        }
    )

    # Force end any active battles
    if fleet.status == FleetStatus.IN_BATTLE.value:
        active_battle = db.query(FleetBattle).filter(
            and_(
                or_(
                    FleetBattle.attacker_fleet_id == fleet_id,
                    FleetBattle.defender_fleet_id == fleet_id
                ),
                FleetBattle.ended_at.is_(None)
            )
        ).first()

        if active_battle:
            active_battle.ended_at = datetime.utcnow()
            active_battle.winner = "draw"

    # Dissolve fleet
    service.disband_fleet(fleet_id)

    return {"message": "Fleet forcefully dissolved"}
