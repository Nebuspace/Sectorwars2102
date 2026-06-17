"""
Player combat API endpoints.

Handles combat initiation and status tracking for players.
Includes planetary assault and sector retreat mechanics.

Combat resolution is synchronous: CombatService resolves the entire fight in
the engage call and persists a CombatLog row. The status endpoint therefore
always reports 'completed' and returns every round for client-side replay —
there is no ongoing-combat entity to poll or retreat from mid-fight.
"""

import json
import random
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.combat_log import CombatLog, CombatOutcome
from src.models.player import Player
from src.models.planet import Planet
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipType
from src.services.combat_service import CombatService
from src.services.movement_service import MovementService
from src.services.planetary_service import PlanetaryService
from src.services.turn_service import spend_turns

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


class CombatRoundEvent(BaseModel):
    """A single event from the round-by-round combat log."""
    round: int
    actor: Optional[str] = None
    action: Optional[str] = None
    message: str


class CombatStatusResponse(BaseModel):
    """Combat status response.

    Combat resolves in a single call, so status is always 'completed' and
    rounds carries the full event log for client-side replay.
    """
    status: str = Field(..., description="'completed' — combat resolves synchronously")
    outcome: Optional[str] = Field(None, description="attacker_win | defender_win | draw | escaped")
    rounds: list[CombatRoundEvent]
    winner: Optional[str] = Field(None, description="Winner player UUID, 'draw', or null")
    combatDuration: Optional[int] = None
    creditsLooted: Optional[int] = None
    cargoLooted: list[str] = Field(default_factory=list)


def _execute_planet_assault(db: Session, player: Player, planet_id: UUID) -> dict:
    """Shared guard + resolution path for planetary assault.

    Both engage(targetType='planet') and POST /assault-planet route through
    here so the no-defenses guard and the canon 3-turn cost (charged inside
    CombatService.attack_planet) stay identical between the two entry points.
    """
    planet = db.query(Planet).filter(Planet.id == planet_id).first()
    if not planet:
        raise HTTPException(status_code=404, detail="Planet not found")

    # Formation-window protection (genesis-devices.md): a forming planet cannot
    # be attacked. Checked before the no-defenses guard so a freshly-formed
    # (still-defenseless) planet returns the canon "still forming" reason rather
    # than "no defenses to assault". attack_planet enforces the same guard for
    # the direct service path.
    if planet.formation_status == 'forming':
        raise HTTPException(status_code=400, detail="This planet is still forming and cannot be attacked")

    # Planet must have defenses worth assaulting — without this guard a
    # defenseless planet would be a free capture
    if (planet.defense_level or 0) <= 0 and (planet.shields or 0) <= 0:
        raise HTTPException(status_code=400, detail="Planet has no defenses to assault")

    # Materialize accrued siege state when the ATTACKER acts (S3): siege morale
    # decay was previously lazy on the VICTIM's reads only (get_siege_status /
    # get_planet_details), so an attacker assaulting a long-besieged planet saw
    # stale morale. Settle the siege here — advance accrued morale decay and
    # re-evaluate siege validity — so the target's morale/vulnerability reflect
    # reality at the moment of attack. check_and_update_siege detects + advances
    # + commits, so the fresh planet query inside attack_planet sees the
    # settled state.
    #
    # CANON GAP (honest note): combat_service._resolve_planet_combat gates
    # capture purely on planet_damage >= planet_defense_level — morale<=0
    # "vulnerability" does NOT shortcut or cheapen capture in the resolution
    # math, and defense.md does not define what siege vulnerability MEANS for a
    # direct assault (only that morale<=0 marks a planet "vulnerable to
    # capture"). With canon silent on the assault×vulnerability interaction we
    # do NOT invent a morale-based capture path; we only settle the siege state
    # so morale/isVulnerable are truthful. Flagged for Max — see return.
    if planet.under_siege:
        try:
            PlanetaryService(db).check_and_update_siege(planet_id)
        except ValueError:
            pass  # unowned/transient — attack_planet's own guards still apply

    # Remaining guards (sector match, ownership, active ship, docked/landed,
    # turn availability) live in CombatService.attack_planet, which charges
    # the canon 3-turn cost and locks the attacker row.
    result = CombatService(db).attack_planet(attacker_id=player.id, planet_id=planet_id)
    if isinstance(result, dict):
        # Surface the canon gap to callers/telemetry without altering capture
        # mechanics (morale vulnerability is settled but not wired to capture).
        result.setdefault(
            "siegeVulnerabilityNote",
            "Siege morale state settled at assault time; canon does not define "
            "a morale-based capture shortcut, so capture remains defense-gated."
        )
    return result


def _get_combat_log_for_player(db: Session, combat_id_raw: str, player: Player) -> CombatLog:
    """Load a CombatLog by id, enforcing that the player was involved."""
    try:
        combat_id = UUID(combat_id_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid combat ID format")

    combat = db.query(CombatLog).filter(CombatLog.id == combat_id).first()
    if not combat:
        raise HTTPException(status_code=404, detail="Combat not found")

    # NPC-defender logs have defender_id NULL — only the attacker may view those
    if combat.attacker_id != player.id and combat.defender_id != player.id:
        raise HTTPException(status_code=403, detail="You are not involved in this combat")

    return combat


# Combat Endpoints

@router.post("/engage", response_model=CombatEngageResponse)
async def engage_combat(
    request: CombatEngageRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Initiate combat with a target.

    'ship' targets are Ship ids from the sector's players_present list —
    player-owned ships route to PvP combat against the owner, NPC ships
    (owner_id NULL / is_npc) resolve against the ship itself.
    """
    try:
        target_id = UUID(request.targetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target ID format")

    service = CombatService(db)

    if request.targetType == "ship":
        ship = db.query(Ship).filter(Ship.id == target_id).first()
        if not ship or ship.is_destroyed:
            raise HTTPException(status_code=404, detail="Target ship not found")

        # NPC ships have no owning player. Guard on owner_id-is-None plus the
        # is_npc flag via getattr so this code does not depend on the NPC
        # slice's Ship model changes having landed.
        is_npc_ship = ship.owner_id is None or bool(getattr(ship, "is_npc", False))
        if is_npc_ship:
            result = service.attack_npc_ship(player.id, ship.id)
        else:
            if ship.owner_id == player.id:
                return CombatEngageResponse(status="error", message="Cannot attack your own ship")
            result = service.attack_player(player.id, ship.owner_id)
    elif request.targetType == "planet":
        result = _execute_planet_assault(db, player, target_id)
    else:
        # Port assault transfers station ownership — economically sensitive,
        # deliberately disabled this pass. Return 501 Not Implemented (not a
        # 200 "error" body) so the client treats it as a permanently-unavailable
        # feature, not a transient/game-logic failure worth retrying.
        raise HTTPException(
            status_code=501,
            detail="Port assault operations are not yet authorized."
        )

    if not result.get("success"):
        return CombatEngageResponse(status="error", message=result.get("message", "Combat failed"))

    return CombatEngageResponse(
        combatId=result.get("combat_log_id"),
        status="initiated",
        message=result.get("message")
    )


@router.get("/{combatId}/status", response_model=CombatStatusResponse)
async def get_combat_status(
    combatId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get the resolved state of a combat.

    Combat resolves synchronously in the engage call, so this always returns
    status 'completed' with the full round-by-round event log.
    """
    combat = _get_combat_log_for_player(db, combatId, player)

    # Round events are stored as JSON in the combat_log Text column
    events: list[CombatRoundEvent] = []
    if combat.combat_log:
        try:
            raw_events = json.loads(combat.combat_log)
        except (ValueError, TypeError):
            raw_events = []
        if isinstance(raw_events, list):
            for entry in raw_events:
                if not isinstance(entry, dict) or "message" not in entry:
                    continue
                events.append(CombatRoundEvent(
                    round=int(entry.get("round", 0)),
                    actor=entry.get("actor"),
                    action=entry.get("action"),
                    message=str(entry["message"])
                ))

    # Winner: UUID-string of the winning player, 'draw', or None (escaped /
    # NPC defender won — NPC defenders have no player UUID)
    winner: Optional[str] = None
    if combat.outcome == CombatOutcome.ATTACKER_WIN.value and combat.attacker_id:
        winner = str(combat.attacker_id)
    elif combat.outcome == CombatOutcome.DEFENDER_WIN.value and combat.defender_id:
        winner = str(combat.defender_id)
    elif combat.outcome == CombatOutcome.DRAW.value:
        winner = "draw"

    cargo_looted = [
        f"{quantity} {resource}"
        for resource, quantity in (combat.cargo_looted or {}).items()
    ]

    return CombatStatusResponse(
        status="completed",
        outcome=combat.outcome,
        rounds=events,
        winner=winner,
        combatDuration=combat.rounds,
        creditsLooted=combat.credits_looted,
        cargoLooted=cargo_looted
    )


class RetreatResponse(BaseModel):
    """Response from retreat attempt."""
    success: bool
    message: str
    retreatChance: Optional[int] = None


@router.post("/{combatId}/retreat", response_model=RetreatResponse)
async def attempt_retreat(
    combatId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Mid-combat retreat is not possible: combat resolves in a single call.

    Escape attempts happen automatically inside the resolution loop (see
    CombatService escape mechanics). Use POST /combat/retreat to flee the
    current sector after combat.
    """
    _get_combat_log_for_player(db, combatId, player)

    return RetreatResponse(
        success=False,
        message="Combat already resolved — use sector retreat to flee your current sector"
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
    db: Session = Depends(get_db)
):
    """
    Assault a planet's defenses.

    Requires being in the same sector as the target planet. The planet must
    be owned by another player and have defenses (defense_level > 0 or
    shields > 0). Costs 3 turns — same path and cost as
    engage(targetType='planet').

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

    # Shared path with engage(targetType='planet'): no-defenses guard +
    # CombatService.attack_planet (canon 3-turn cost, attacker row lock,
    # ownership/sector/ship/docked guards)
    result = _execute_planet_assault(db, player, pid)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Assault failed"))

    return PlanetaryAssaultResponse(
        success=True,
        message=result["message"],
        combatResult=result.get("combat_result"),
        combatDetails=result.get("combat_details"),
        planetCaptured=result.get("planet_captured", False),
        turnsConsumed=result.get("turns_consumed"),
        turnsRemaining=result.get("turns_remaining"),
        combatLogId=result.get("combat_log_id")
    )


# --- Sector Drone Combat (clear hostile drones) ---

class SectorDroneAttackResponse(BaseModel):
    """Response from a sector-drone engagement."""
    success: bool
    message: str
    combatResult: Optional[str] = None
    combatDetails: Optional[list] = None
    dronesDestroyed: Optional[int] = None
    dronesRemaining: Optional[int] = None
    turnsConsumed: Optional[int] = None
    turnsRemaining: Optional[int] = None
    combatLogId: Optional[str] = None


@router.post("/attack-sector-drones", response_model=SectorDroneAttackResponse)
async def attack_sector_drones(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Attack the hostile drones deployed in your current sector.

    A 2-turn PvE engagement: your ship fights every live drone deployed in the
    sector that you do not own. Drones are destroyed per their own combat stats
    and your ship takes hull damage in return. Requires an active ship, being
    undocked and not landed, and at least one hostile drone in the sector.

    Clearing the sector of hostile drones awards +10 personal reputation
    (destroy_pirate_drones). Turn cost and combat resolution are charged inside
    CombatService.attack_sector_drones, which locks the attacker row.
    """
    if player.current_sector_id is None:
        raise HTTPException(status_code=400, detail="You are not in a sector")

    result = CombatService(db).attack_sector_drones(
        attacker_id=player.id, sector_id=player.current_sector_id
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Drone attack failed"))

    return SectorDroneAttackResponse(
        success=True,
        message=result["message"],
        combatResult=result.get("combat_result"),
        combatDetails=result.get("combat_details"),
        dronesDestroyed=result.get("drones_destroyed"),
        dronesRemaining=result.get("drones_remaining"),
        turnsConsumed=result.get("turns_consumed"),
        turnsRemaining=result.get("turns_remaining"),
        combatLogId=result.get("combat_log_id"),
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
    db: Session = Depends(get_db)
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
        spend_turns(player, turn_cost)
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
    spend_turns(player, turn_cost)

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

        # Move the player. Update sector presence via the movement service
        # helper so the old sector doesn't keep a ghost players_present entry
        # (setting current_sector_id alone leaves the player listed — and
        # targetable — in the sector they just fled).
        MovementService(db)._update_player_presence(
            player, player.current_sector_id, target_sector.sector_id
        )
        player.current_sector_id = target_sector.sector_id
        # Keep current_region_id in sync — region-filtered routes like
        # /player/current-sector 404 on a stale region
        player.current_region_id = target_sector.region_id
        # Keep the ship row in sync too — sector views read Ship.sector_id,
        # and a ship left behind in the fled sector renders a ghost
        if player.current_ship:
            player.current_ship.sector_id = target_sector.sector_id
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