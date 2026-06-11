"""
Player combat API endpoints.

Handles combat initiation and status tracking for players.
Includes planetary assault and sector retreat mechanics.
"""

import random
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_async_session
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.planet import Planet
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipType
from src.services.player_combat_service import PlayerCombatService
from src.services.combat_service import CombatService

# Mounted under the /api/v1 api_router — a "/api/combat" prefix here doubled
# up to /api/v1/api/combat, which no client called.
router = APIRouter(prefix="/combat", tags=["player-combat"])


# Request/Response Models

class CombatEngageRequest(BaseModel):
    """Request to engage in combat."""
    targetType: str = Field(..., pattern="^(ship|planet|port)$", description="Type of target")
    targetId: str = Field(..., description="UUID of the target")


class CombatEngageResponse(BaseModel):
    """Response from combat engagement."""
    combatId: Optional[str] = None
    status: str = Field(..., description="'initiated' or 'error'")
    message: Optional[str] = None


class CombatRound(BaseModel):
    """Single round of combat."""
    round: int
    attackerHits: int
    defenderHits: int
    attackerDamage: int
    defenderDamage: int
    attackerShields: int
    defenderShields: int
    attackerArmor: int
    defenderArmor: int
    criticalHit: bool
    specialEvent: Optional[str] = None


class CombatStatusResponse(BaseModel):
    """Combat status response."""
    status: str = Field(..., description="'ongoing' or 'completed'")
    rounds: list[CombatRound]
    winner: Optional[str] = None
    combatDuration: Optional[int] = None
    creditsLooted: Optional[int] = None
    cargoLooted: Optional[list] = None


# Combat Endpoints

@router.post("/engage", response_model=CombatEngageResponse)
async def engage_combat(
    request: CombatEngageRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Initiate combat with a target."""
    service = PlayerCombatService(db)
    
    try:
        # Convert string UUID to UUID object
        target_id = UUID(request.targetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target ID format")
    
    result = service.initiate_combat(
        attacker_id=player.id,
        target_type=request.targetType,
        target_id=target_id
    )
    
    return CombatEngageResponse(
        combatId=result.get("combatId"),
        status=result.get("status", "error"),
        message=result.get("message")
    )


@router.get("/{combatId}/status", response_model=CombatStatusResponse)
async def get_combat_status(
    combatId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Get the current status of a combat."""
    service = PlayerCombatService(db)
    
    try:
        # Convert string UUID to UUID object
        combat_id = UUID(combatId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid combat ID format")
    
    # Verify player is involved in this combat
    from src.models.combat_log import CombatLog
    combat = db.query(CombatLog).filter(CombatLog.id == combat_id).first()
    if not combat:
        raise HTTPException(status_code=404, detail="Combat not found")
        
    if combat.attacker_id != player.id and combat.defender_id != player.id:
        raise HTTPException(status_code=403, detail="You are not involved in this combat")
    
    try:
        status = service.get_combat_status(combat_id)

        return CombatStatusResponse(
            status=status["status"],
            rounds=[CombatRound(**round_data) for round_data in status["rounds"]],
            winner=status.get("winner"),
            combatDuration=status.get("combatDuration"),
            creditsLooted=status.get("creditsLooted"),
            cargoLooted=status.get("cargoLooted", [])
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class RetreatResponse(BaseModel):
    """Response from retreat attempt."""
    success: bool
    message: str
    retreatChance: Optional[int] = None


@router.post("/{combatId}/retreat", response_model=RetreatResponse)
async def attempt_retreat(
    combatId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """Attempt to retreat from an ongoing combat."""
    service = PlayerCombatService(db)

    try:
        combat_id = UUID(combatId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid combat ID format")

    # Verify player is involved in this combat
    from src.models.combat_log import CombatLog
    combat = db.query(CombatLog).filter(CombatLog.id == combat_id).first()
    if not combat:
        raise HTTPException(status_code=404, detail="Combat not found")

    if combat.attacker_id != player.id and combat.defender_id != player.id:
        raise HTTPException(status_code=403, detail="You are not involved in this combat")

    result = service.attempt_retreat(
        combat_id=combat_id,
        player_id=player.id
    )

    return RetreatResponse(
        success=result["success"],
        message=result["message"],
        retreatChance=result.get("retreatChance")
    )


# --- Planetary Assault ---

class PlanetaryAssaultResponse(BaseModel):
    """Response from a planetary assault."""
    success: bool
    message: str
    combatResult: Optional[str] = None
    combatDetails: Optional[list] = None
    planetCaptured: Optional[bool] = None
    turnsConsumed: Optional[int] = None
    turnsRemaining: Optional[int] = None
    combatLogId: Optional[str] = None


@router.post("/assault-planet/{planet_id}", response_model=PlanetaryAssaultResponse)
async def assault_planet(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """
    Assault a planet's defenses.

    Requires being in the same sector as the target planet. The planet must
    be owned by another player and have defenses (defense_level > 0 or
    shields > 0). Costs 5 turns.

    Combat outcome is determined by the player's ship firepower versus
    the planet's defense_level and shields. On success, planet defense_level
    is reduced. If defenses are fully overcome, the planet is captured.
    On failure, the attacker's ship takes hull damage.
    """
    # Validate planet_id format
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    # Lock player row to prevent concurrent turn deduction races
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Fetch the planet
    planet = db.query(Planet).filter(Planet.id == pid).first()
    if not planet:
        raise HTTPException(status_code=404, detail="Planet not found")

    # Verify player is in the same sector
    if player.current_sector_id != planet.sector_id:
        raise HTTPException(
            status_code=400,
            detail="You must be in the planet's sector to assault it"
        )

    # Planet must have defenses worth assaulting
    planet_defense_level = planet.defense_level or 0
    planet_shields = planet.shields or 0
    if planet_defense_level <= 0 and planet_shields <= 0:
        raise HTTPException(
            status_code=400,
            detail="Planet has no defenses to assault"
        )

    # Cannot assault your own planet
    if planet.owner_id and planet.owner_id == player.id:
        raise HTTPException(status_code=400, detail="Cannot assault your own planet")

    # Must have an active ship
    if not player.current_ship:
        raise HTTPException(status_code=400, detail="No active ship selected")

    # Cannot assault while docked or landed
    if player.is_docked or player.is_landed:
        raise HTTPException(
            status_code=400,
            detail="Cannot assault while docked at a port or landed on a planet"
        )

    # Turn cost for planetary assault is 5
    turn_cost = 5
    if player.turns < turn_cost:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough turns. Planetary assault costs {turn_cost} turns, you have {player.turns}"
        )

    # Delegate to CombatService.attack_planet which handles full combat resolution
    combat_service = CombatService(db)
    result = combat_service.attack_planet(
        attacker_id=player.id,
        planet_id=pid
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Assault failed"))

    # Apply the additional turn cost difference (attack_planet uses 3, we want 5)
    # CombatService.attack_planet already deducted 3 turns, add 2 more
    extra_turns = turn_cost - 3
    if extra_turns > 0:
        player.turns -= extra_turns
        db.commit()

    return PlanetaryAssaultResponse(
        success=True,
        message=result["message"],
        combatResult=result.get("combat_result"),
        combatDetails=result.get("combat_details"),
        planetCaptured=result.get("planet_captured", False),
        turnsConsumed=turn_cost,
        turnsRemaining=player.turns,
        combatLogId=result.get("combat_log_id")
    )


# --- Sector Retreat (flee current sector) ---

class SectorRetreatResponse(BaseModel):
    """Response from a sector retreat attempt."""
    success: bool
    message: str
    newSectorId: Optional[int] = None
    escapeChance: Optional[int] = None
    turnsConsumed: int
    turnsRemaining: int


@router.post("/retreat", response_model=SectorRetreatResponse)
async def retreat_from_sector(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_async_session)
):
    """
    Attempt to retreat from the current sector to a random connected sector.

    Uses an escape chance calculation based on the player's ship speed and
    type. Faster, more agile ships (FAST_COURIER, SCOUT_SHIP) have a better
    chance of escaping. Costs 3 turns regardless of outcome.

    On success the player is moved to a random warp-connected sector.
    On failure the player remains in the current sector.
    """
    # Lock player row to prevent concurrent turn deduction races
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Must have an active ship
    if not player.current_ship:
        raise HTTPException(status_code=400, detail="No active ship selected")

    # Cannot retreat while docked or landed
    if player.is_docked or player.is_landed:
        raise HTTPException(
            status_code=400,
            detail="Cannot retreat while docked at a port or landed on a planet"
        )

    # Turn cost
    turn_cost = 3
    if player.turns < turn_cost:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough turns. Retreat costs {turn_cost} turns, you have {player.turns}"
        )

    # Find the player's current sector
    current_sector = db.query(Sector).filter(
        Sector.sector_id == player.current_sector_id
    ).first()
    if not current_sector:
        raise HTTPException(status_code=500, detail="Current sector not found")

    # Find connected sectors via the sector_warps association table
    connected_rows = db.execute(
        sector_warps.select().where(
            or_(
                sector_warps.c.source_sector_id == current_sector.id,
                sector_warps.c.destination_sector_id == current_sector.id
            )
        )
    ).fetchall()

    # Collect the UUIDs of connected sectors
    connected_sector_uuids = set()
    for row in connected_rows:
        if row.source_sector_id == current_sector.id:
            connected_sector_uuids.add(row.destination_sector_id)
        else:
            # Only include bidirectional warps when traversing in reverse
            if row.is_bidirectional:
                connected_sector_uuids.add(row.source_sector_id)

    if not connected_sector_uuids:
        # Deduct turns even though there's nowhere to go
        player.turns -= turn_cost
        db.commit()
        return SectorRetreatResponse(
            success=False,
            message="No connected sectors to retreat to. You are trapped!",
            newSectorId=None,
            escapeChance=0,
            turnsConsumed=turn_cost,
            turnsRemaining=player.turns
        )

    # Calculate escape chance based on ship characteristics
    ship = player.current_ship
    base_chance = 50  # 50% base chance

    # Speed bonus: up to +25%
    ship_speed = ship.current_speed if hasattr(ship, 'current_speed') and ship.current_speed else 1.0
    speed_bonus = min(25, int(ship_speed * 10))

    # Ship type bonus: fast/agile ships get +15%
    fast_types = {ShipType.FAST_COURIER, ShipType.SCOUT_SHIP}
    type_bonus = 15 if hasattr(ship, 'type') and ship.type in fast_types else 0

    escape_chance = max(10, min(90, base_chance + speed_bonus + type_bonus))

    # Deduct turns
    player.turns -= turn_cost

    # Roll for escape
    roll = random.randint(1, 100)
    if roll <= escape_chance:
        # Success - move to a random connected sector
        target_uuid = random.choice(list(connected_sector_uuids))
        target_sector = db.query(Sector).filter(Sector.id == target_uuid).first()

        if not target_sector:
            db.commit()
            return SectorRetreatResponse(
                success=False,
                message="Retreat navigation failed - destination sector not found.",
                newSectorId=None,
                escapeChance=escape_chance,
                turnsConsumed=turn_cost,
                turnsRemaining=player.turns
            )

        # Move the player
        player.current_sector_id = target_sector.sector_id
        db.commit()

        return SectorRetreatResponse(
            success=True,
            message=f"Retreat successful! You escaped to sector {target_sector.sector_id}.",
            newSectorId=target_sector.sector_id,
            escapeChance=escape_chance,
            turnsConsumed=turn_cost,
            turnsRemaining=player.turns
        )
    else:
        # Failure - remain in current sector
        db.commit()

        return SectorRetreatResponse(
            success=False,
            message="Retreat failed! You remain in the current sector.",
            newSectorId=None,
            escapeChance=escape_chance,
            turnsConsumed=turn_cost,
            turnsRemaining=player.turns
        )