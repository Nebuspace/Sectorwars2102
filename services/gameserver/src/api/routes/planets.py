"""
Planetary management API endpoints.

Handles planet colonization, resource allocation, building construction,
defenses, sieges, and landing/departing operations.
"""

import logging
from datetime import datetime, UTC
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.planet import Planet, PlanetStatus
from src.models.ship import Ship
from src.services.planetary_service import (
    PlanetaryService,
    max_colonists_for,
    max_population_for,
)

router = APIRouter(prefix="/planets", tags=["planets"])

logger = logging.getLogger(__name__)

# Traditional colonization requirements (FEATURES/planets/colonization.md
# "#1-traditional-colonization" / "#fulfilling-the-contract"):
#   - "10,000 credits investment (the founding-grant fee paid to the
#      destination claim)"
#   - pioneers delivered in cargo (1 colonist = 1 cargo unit, bought at the
#     capital's CLASS_0 station Pioneer Office)
# Interpretation note: the doc's full migration contract is 10,000 pioneers,
# which is multi-trip by design (ships carry <= 1,000 colonists). We implement
# the doc-faithful core: a claim requires the founding-grant fee AND at least
# 100 colonists aboard the current ship (the minimum viable Outpost seed —
# "new colony = Citadel L1 'Outpost' with 100-1,000 starting population").
# On success ALL aboard colonists transfer to the planet, capped at the L1
# Outpost max_colonists of 1,000 (ADR-0035); the rest of the 10,000-pioneer
# cohort arrives over subsequent trips as the citadel grows.
CLAIM_CREDIT_COST = 10_000
CLAIM_MIN_COLONISTS = 100


# Request/Response Models

class PlanetResourceAllocation(BaseModel):
    """Resource allocation for colonists."""
    fuel: int = Field(..., ge=0)
    organics: int = Field(..., ge=0)
    equipment: int = Field(..., ge=0)


class BuildingUpgradeRequest(BaseModel):
    """Building upgrade request."""
    buildingType: str = Field(..., pattern="^(factory|farm|mine|defense|research)$")
    targetLevel: int = Field(..., ge=1, le=10)


class DefenseUpdateRequest(BaseModel):
    """Defense update request."""
    turrets: Optional[int] = Field(None, ge=0)
    shields: Optional[int] = Field(None, ge=0)
    fighters: Optional[int] = Field(None, ge=0)


class GenesisDeployRequest(BaseModel):
    """Genesis device deployment request (legacy - use /genesis/deploy instead)."""
    sectorId: str
    planetName: str = Field(..., min_length=3, max_length=50)
    # basic = 1 device, enhanced = 3 devices, advanced = 1 device + the Colony
    # Ship is sacrificed for an instant Settlement-level colony.
    tier: str = Field(default="basic", pattern="^(basic|enhanced|advanced)$")
    # Biome is rolled server-side from the device tier (ADR-0014); kept optional
    # only so older clients that still send a type don't 422.
    planetType: str | None = None
    # Colonial Registry visibility (FROZEN registry contract): "registered"
    # (default, visible), "clandestine" (hidden from registry lookup), or
    # "chartered" (publicly protected, reputation-scaled fee). Older clients
    # that omit it default to "registered".
    registration: str = Field(default="registered", pattern="^(clandestine|registered|chartered)$")


class SpecializationRequest(BaseModel):
    """Planet specialization request."""
    specialization: str = Field(..., pattern="^(agricultural|industrial|military|research|balanced)$")


class LandRequest(BaseModel):
    """Planet landing request."""
    planet_id: str


class LandResponse(BaseModel):
    """Planet landing response."""
    success: bool
    message: str
    planet_id: str
    planet_name: str
    planet_type: str
    habitability_score: int
    population: int
    owner_id: Optional[str] = None
    is_owned_by_player: bool
    # Population-hub affordances: the client renders the Population Center UI
    # (Pioneer Office) instead of the generic colony console when this is set.
    is_population_hub: bool = False
    services: List[str] = Field(default_factory=list)


class ClaimResponse(BaseModel):
    """Planet claim response."""
    success: bool
    message: str
    planet_id: str
    planet_name: str
    planet_type: str
    habitability_score: int
    population: int
    is_landed: bool
    colonists_settled: int
    credits_spent: int


class ColonistTransferRequest(BaseModel):
    """Colonist transfer between ship cargo and planet."""
    action: str = Field(..., pattern="^(embark|disembark)$")
    quantity: int = Field(..., gt=0)


class ColonistTransferResponse(BaseModel):
    """Colonist transfer response."""
    planet_colonists: int
    ship_colonists: int
    max_colonists: int
    message: str


class LeaveResponse(BaseModel):
    """Planet departure response."""
    success: bool
    message: str
    sector_id: int


class RenameRequest(BaseModel):
    """Planet rename request."""
    name: str = Field(..., min_length=1, max_length=50)


class RenameResponse(BaseModel):
    """Planet rename response."""
    success: bool
    message: str
    planet_id: str
    old_name: str
    new_name: str


# Planet Landing/Departure Endpoints

@router.post("/{planet_id}/claim", response_model=ClaimResponse)
async def claim_planet(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Claim an unclaimed planet via traditional colonization and land on it.

    This is required before landing on any unclaimed planet.
    The player is automatically landed on the planet after claiming.

    Requirements (FEATURES/planets/colonization.md "Traditional colonization"):
    - Player must be in the same sector as the planet
    - Player must not be docked at a station
    - Player must not already be landed on a planet
    - Planet must be unclaimed (no owner)
    - Planet must be habitable (not uninhabitable, gas giant, or restricted)
    - Planet must not be a capital population hub (never claimable)
    - Player must pay the 10,000-credit founding-grant fee
    - Current ship must carry at least 100 colonists in cargo

    On success all aboard colonists settle (capped at the L1 Outpost
    max_colonists of 1,000 per ADR-0035) and the colony starts as a
    Citadel Level 1 Outpost.
    """
    from src.models.planet import PlanetType, player_planets
    from src.services.citadel_service import CITADEL_LEVELS

    try:
        planet_uuid = UUID(planet_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid planet ID format"
        )

    # Check if player is already docked
    if player.is_docked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must undock from the station before claiming a planet"
        )

    # Check if player is already landed
    if player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already landed on a planet. Leave first before claiming another."
        )

    # Get the planet — locked, so two pilots can't both pass the
    # unclaimed check and double-found the colony (lost-update race)
    planet = db.query(Planet).filter(Planet.id == planet_uuid).with_for_update().first()
    if not planet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Planet not found"
        )

    # Check if player is in the same sector as the planet
    if planet.sector_id != player.current_sector_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Planet is not accessible from your current location"
        )

    # Check if planet is already owned
    if planet.owner_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This planet is already claimed by another player"
        )

    # RESTRICTED worlds are off-limits; UNINHABITABLE rocks ARE claimable —
    # the canon growth table (FEATURES/planets/colonization #population-growth)
    # lists rates down to VOLCANIC ~0.17%/day and BARREN 0%, and terraforming
    # exists precisely to lift marginal worlds
    if planet.status == PlanetStatus.RESTRICTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot claim this planet: status is {planet.status.value}"
        )

    # Gas giants cannot be claimed
    if planet.type == PlanetType.GAS_GIANT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot claim a gas giant planet"
        )

    # Capital population hubs are public and never claimable
    # (SYSTEMS/galaxy-generation.md Step 8: "Public, well-policed,
    # non-destructible"). Belt-and-braces: any capital-scale population
    # (>= 1,000,000) is treated as a hub even if the flag was missed.
    if planet.is_population_hub or (planet.population or 0) >= 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{planet.name} is a chartered population hub under regional "
                "administration. Its billions of citizens are not looking for "
                "a new landlord."
            )
        )

    # Lock the player row to prevent concurrent credit races
    # (mirrors trading.py's with_for_update pattern)
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Founding-grant fee: "10,000 credits investment (the founding-grant fee
    # paid to the destination claim)" — colonization.md, Traditional colonization
    if player.credits < CLAIM_CREDIT_COST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Claiming a planet requires a {CLAIM_CREDIT_COST:,}-credit "
                f"founding grant. You have {player.credits:,}."
            )
        )

    # Pioneers must be delivered in the current ship's cargo
    # (1 colonist = 1 cargo unit, riding in cryosleep transit pods)
    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id,
        Ship.owner_id == player.id
    ).first()
    if not ship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active ship found"
        )

    cargo = ship.cargo or {'used': 0, 'capacity': 50, 'contents': {}}
    contents = cargo.get('contents', {})
    colonists_aboard = contents.get('colonists', 0)

    if colonists_aboard < CLAIM_MIN_COLONISTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Founding a colony requires at least {CLAIM_MIN_COLONISTS} "
                f"colonists aboard your ship. You are carrying "
                f"{colonists_aboard}. Pioneer migration contracts are issued "
                "at your region's Capital Sector."
            )
        )

    # --- All requirements met: execute the claim ---

    # Deduct the founding-grant fee
    player.credits -= CLAIM_CREDIT_COST

    # Settle ALL aboard colonists, capped at the L1 Outpost workforce
    # ceiling of 1,000 (ADR-0035: "max_colonists = 1,000 (L1 Outpost cap)").
    # Any overflow stays in cryosleep aboard — the 10,000-pioneer migration
    # contract is multi-trip by design.
    colony_level = planet.citadel_level if (planet.citadel_level or 0) >= 1 else 1
    settle_cap = max_colonists_for(colony_level)
    colonists_settled = min(colonists_aboard, max(0, settle_cap - (planet.colonists or 0)))

    contents['colonists'] = colonists_aboard - colonists_settled
    cargo['contents'] = contents
    cargo['used'] = max(0, cargo.get('used', 0) - colonists_settled)
    ship.cargo = cargo
    flag_modified(ship, 'cargo')

    planet.colonists = (planet.colonists or 0) + colonists_settled
    # Migration-contract ledger: attribute the just-settled pioneers to the
    # player's open contracts FIFO (advances `delivered`). Best-effort — a
    # ledger hiccup must never block a colony founding.
    try:
        from src.services import pioneer_service
        pioneer_service.attribute_settlement(db, player.id, colonists_settled)
    except Exception:
        logger.exception("Migration-contract attribution failed on claim")
    # Dual ceilings at colonization (ADR-0035 "Genesis and colonization
    # initialization"): max_colonists = L1 cap; max_population =
    # habitability_score × 1,000.
    planet.max_colonists = settle_cap
    planet.max_population = max_population_for(planet.habitability_score)
    # Simplification: total demographic starts at the settled workforce
    planet.population = max(planet.population or 0, planet.colonists)
    # Anchor lazy growth at the moment of founding
    planet.last_growth_at = datetime.now(UTC)

    # New colony = Citadel Level 1 "Outpost" (colonization.md: "Result:
    # Outpost (Phase 1, citadel level 1)")
    if not planet.citadel_level:
        level_1 = CITADEL_LEVELS[1]
        planet.citadel_level = 1
        planet.citadel_safe_max = level_1["safe_storage"]
        planet.citadel_drone_capacity = level_1["drone_capacity"]
        planet.citadel_max_population = level_1["max_population"]

    # Claim the planet - set owner_id and add to player_planets association
    planet.owner_id = player.id
    planet.status = PlanetStatus.COLONIZED
    planet.colonized_at = db.query(func.now()).scalar()

    # Add to player_planets association table
    db.execute(
        player_planets.insert().values(
            player_id=player.id,
            planet_id=planet.id
        )
    )

    # Auto-land the player on the newly claimed planet
    player.is_landed = True
    player.current_planet_id = planet.id

    db.commit()
    db.refresh(player)
    db.refresh(planet)

    return ClaimResponse(
        success=True,
        message=(
            f"Successfully claimed and landed on {planet.name}. "
            f"{colonists_settled:,} colonists have settled your new Outpost!"
        ),
        planet_id=str(planet.id),
        planet_name=planet.name,
        planet_type=planet.type.value,
        habitability_score=planet.habitability_score,
        population=planet.population,
        is_landed=True,
        colonists_settled=colonists_settled,
        credits_spent=CLAIM_CREDIT_COST
    )


@router.post("/{planet_id}/colonists/transfer", response_model=ColonistTransferResponse)
async def transfer_colonists(
    planet_id: str,
    request: ColonistTransferRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Transfer colonists between the current ship's cargo and a planet.

    Actions (matches player-client GameContext.transferColonists):
    - disembark: move colonists from ship cargo onto the planet
    - embark: move colonists from the planet into ship cargo

    Requirements:
    - Player must be landed on this planet
    - Player must own the planet, or be on the owner's team
    - Colonists are cargo: 1 colonist = 1 cargo unit

    Ceilings enforced per ADR-0035: colonists <= max_colonists (citadel cap),
    population <= max_population (habitability cap), colonists <= population.
    """
    try:
        planet_uuid = UUID(planet_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid planet ID format"
        )

    # Fail fast under row-lock contention instead of blocking until the gateway
    # times out: a 504'd transfer whose FOR UPDATE is wedged can leak its lock
    # and stall every later transfer on this planet (the disembark-hang bug).
    # lock_timeout is LOCAL — it covers the player lock below in the same txn too.
    # Locked: owner AND teammates may transfer concurrently — without the
    # planet lock two embarks can both read N and write N-q (duplication).
    try:
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        planet = db.query(Planet).filter(Planet.id == planet_uuid).with_for_update().first()
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This planet is busy with another colonist transfer — try again in a moment."
        )
    if not planet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Planet not found"
        )

    # Player must be landed on this specific planet
    if not player.is_landed or player.current_planet_id != planet.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be landed on this planet to transfer colonists"
        )

    # Ownership gate: owner, or member of the owner's team (mirrors the
    # owner/team friendliness logic used in siege detection)
    if planet.owner_id != player.id:
        owner = db.query(Player).filter(Player.id == planet.owner_id).first() if planet.owner_id else None
        same_team = (
            owner is not None
            and owner.team_id is not None
            and player.team_id == owner.team_id
        )
        if not same_team:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not own this planet"
            )

    # Lock the player row to serialize concurrent transfers on the same
    # ship/planet pair (mirrors trading.py's with_for_update pattern).
    # Same lock_timeout (set LOCAL above) applies — fail fast, don't hang.
    try:
        player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your ship is busy with another transfer — try again in a moment."
        )

    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id,
        Ship.owner_id == player.id
    ).first()
    if not ship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active ship found"
        )

    # Apply any colonist growth accrued since the last read before
    # validating against the ceilings (lazy growth, ADR-0035 ceilings)
    service = PlanetaryService(db)
    service.apply_population_growth(planet)

    # Settle banked terraforming ticks at the CURRENT population rate BEFORE
    # the embark/disembark changes population (T2): the lazy terraforming
    # advance scales habitability gain by population, so reconciling after the
    # transfer would settle ticks earned under the old population at the new
    # rate (a retroactive rate change a player could game by timing transfers).
    if planet.terraforming_active:
        from src.services.terraforming_service import TerraformingService
        TerraformingService(db).settle_terraforming(planet)

    cargo = ship.cargo or {'used': 0, 'capacity': 50, 'contents': {}}
    contents = cargo.get('contents', {})
    ship_colonists = contents.get('colonists', 0)
    cargo_used = cargo.get('used', 0)
    cargo_capacity = cargo.get('capacity', 50)

    quantity = request.quantity
    planet_colonists = planet.colonists or 0
    # Citadel-tier cap for established colonies; genesis-formed colonies
    # (no citadel yet) keep their stored cap instead of freezing at 0
    citadel_cap = (
        max_colonists_for(planet.citadel_level)
        if (planet.citadel_level or 0) >= 1
        else (planet.max_colonists or 0)
    )
    habitability_cap = max_population_for(planet.habitability_score)

    if request.action == "disembark":
        if ship_colonists < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only {ship_colonists} colonists aboard; cannot disembark {quantity}"
            )
        if planet_colonists + quantity > citadel_cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Citadel level {planet.citadel_level or 0} supports at most "
                    f"{citadel_cap:,} colonists ({planet_colonists:,} settled). "
                    "Upgrade the citadel to house more."
                )
            )
        # population grows with the settled colonists, so the habitability-
        # derived demographic ceiling binds too (ADR-0035 invariants)
        if planet_colonists + quantity > habitability_cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Habitability {planet.habitability_score} caps total "
                    f"population at {habitability_cap:,}. Terraform the planet "
                    "to raise the ceiling."
                )
            )

        contents['colonists'] = ship_colonists - quantity
        cargo['used'] = max(0, cargo_used - quantity)
        planet.colonists = planet_colonists + quantity
        # Simplification: total demographic tracks the workforce floor
        planet.population = max(planet.population or 0, planet.colonists)
        # Migration-contract ledger: settling pioneers advances `delivered`
        # on the player's open contracts FIFO. Best-effort.
        try:
            from src.services import pioneer_service
            pioneer_service.attribute_settlement(db, player.id, quantity)
        except Exception:
            logger.exception("Migration-contract attribution failed on disembark")
        message = f"{quantity:,} colonists disembarked onto {planet.name}"
    else:  # embark
        if planet_colonists < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only {planet_colonists:,} colonists on {planet.name}; cannot embark {quantity}"
            )
        free_space = cargo_capacity - cargo_used
        if free_space < quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient cargo space. Have {free_space} free, need {quantity}"
            )

        contents['colonists'] = ship_colonists + quantity
        cargo['used'] = cargo_used + quantity
        planet.colonists = planet_colonists - quantity
        # Departing colonists leave the demographic count too, but
        # population never drops below the remaining workforce
        planet.population = max(planet.colonists, (planet.population or 0) - quantity)

        # Clamp production allocations to the reduced colonist count, preserving
        # ratios (canon planetary-production-tick.md:212: "Clamp allocations to
        # current colonist count proportionally; preserve ratios"). Fewer
        # colonists cannot work more allocation slots than remain.
        new_colonists = planet.colonists or 0
        fuel_alloc = planet.fuel_allocation or 0
        organics_alloc = planet.organics_allocation or 0
        equipment_alloc = planet.equipment_allocation or 0
        alloc_sum = fuel_alloc + organics_alloc + equipment_alloc
        if alloc_sum > new_colonists:
            if new_colonists <= 0:
                planet.fuel_allocation = 0
                planet.organics_allocation = 0
                planet.equipment_allocation = 0
            else:
                # Floor each proportionally, then distribute the remainder in a
                # stable order (fuel -> organics -> equipment) so the sum lands
                # exactly on new_colonists without exceeding it.
                scaled = [
                    (fuel_alloc * new_colonists) // alloc_sum,
                    (organics_alloc * new_colonists) // alloc_sum,
                    (equipment_alloc * new_colonists) // alloc_sum,
                ]
                remainder = new_colonists - sum(scaled)
                for i in range(remainder):
                    scaled[i % 3] += 1
                planet.fuel_allocation = scaled[0]
                planet.organics_allocation = scaled[1]
                planet.equipment_allocation = scaled[2]

        message = f"{quantity:,} colonists embarked from {planet.name}"

    cargo['contents'] = contents
    ship.cargo = cargo
    flag_modified(ship, 'cargo')

    db.commit()
    db.refresh(planet)
    db.refresh(ship)

    return ColonistTransferResponse(
        planet_colonists=planet.colonists,
        ship_colonists=(ship.cargo or {}).get('contents', {}).get('colonists', 0),
        max_colonists=citadel_cap,
        message=message
    )


def _rename_planet_by_discoverer(planet_id: str, request: RenameRequest,
                                 player: Player, db: Session) -> RenameResponse:
    """ADR-0073 rename: ONLY the planet's discoverer may set its name (claimed
    or not). Writes ``custom_name`` (the auto-name is preserved); display
    resolves custom_name -> auto_name -> name. New name 1-50 chars."""
    try:
        planet_uuid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid planet ID format")

    planet = db.query(Planet).filter(Planet.id == planet_uuid).with_for_update().first()
    if not planet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planet not found")

    # Discoverer-only (NOT owner) — claimed or not.
    if planet.discovered_by is None or planet.discovered_by != player.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the discoverer of this planet may rename it",
        )

    old_name = planet.display_name
    new_name = (request.name or "").strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Planet name cannot be empty")
    if len(new_name) > 50:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Planet name must be 50 characters or fewer")

    planet.custom_name = new_name
    db.commit()
    db.refresh(planet)
    return RenameResponse(
        success=True,
        message=f"Planet renamed from '{old_name}' to '{new_name}'",
        planet_id=str(planet.id),
        old_name=old_name,
        new_name=new_name,
    )


@router.post("/{planet_id}/name", response_model=RenameResponse)
async def name_planet(
    planet_id: str,
    request: RenameRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Set a planet's name (ADR-0073). Authority: the planet's discoverer only,
    claimed or not. Canonical No-Man's-Sky naming endpoint."""
    return _rename_planet_by_discoverer(planet_id, request, player, db)


@router.put("/{planet_id}/rename", response_model=RenameResponse)
async def rename_planet(
    planet_id: str,
    request: RenameRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Rename a planet (legacy path, now discoverer-gated + custom_name to match
    ADR-0073). Prefer POST /{planet_id}/name."""
    return _rename_planet_by_discoverer(planet_id, request, player, db)


@router.post("/land", response_model=LandResponse)
async def land_on_planet(
    request: LandRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Land on a planet in the current sector.

    Requirements:
    - Player must be in the same sector as the planet
    - Player must not be docked at a station
    - Player must not already be landed on a planet
    - Planet must be habitable or colonized (not uninhabitable or restricted)
    - Planet must be owned (unclaimed planets require claiming first via POST /planets/{id}/claim)
    """
    try:
        planet_id = UUID(request.planet_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid planet ID format"
        )

    # Check if player is already docked
    if player.is_docked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must undock from the station before landing on a planet"
        )

    # Check if player is already landed
    if player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already landed on a planet. Leave first before landing elsewhere."
        )

    # Get the planet
    planet = db.query(Planet).filter(Planet.id == planet_id).first()
    if not planet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Planet not found"
        )

    # Check if player is in the same sector as the planet
    if planet.sector_id != player.current_sector_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Planet is not accessible from your current location"
        )

    # RESTRICTED worlds are off-limits; UNINHABITABLE rocks are landable
    # (environment suits, sealed habitats — same rationale as the claim gate)
    if planet.status == PlanetStatus.RESTRICTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot land on this planet: status is {planet.status.value}"
        )

    # Gas giants cannot be landed on
    from src.models.planet import PlanetType
    if planet.type == PlanetType.GAS_GIANT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot land on a gas giant planet"
        )

    # Check if planet is still forming (genesis device)
    if planet.formation_status == "forming":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This planet is still forming and cannot be landed on yet. Check formation status at GET /genesis/status/{planet_id}"
        )

    # Check if planet is unclaimed - require claiming first.
    # EXCEPTION: capital population hubs (the TERRA Capital-welcome planet)
    # are public and can never be claimed (see the claim guard above), yet
    # canon makes the Capital Sector the welcome hub where new arrivals dock
    # and brokers the colonist migration contracts at the Pioneer Office
    # (FEATURES/planets/colonization.md). They must be landable by anyone
    # without ownership. Belt-and-braces population check mirrors the claim
    # guard so a missed flag can't re-strand the hub.
    is_population_hub = planet.is_population_hub or (planet.population or 0) >= 1_000_000
    if planet.owner_id is None and not is_population_hub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This planet is unclaimed. You must claim it first before landing. Use POST /planets/{id}/claim"
        )

    # Perform landing
    player.is_landed = True
    player.current_planet_id = planet.id

    db.commit()
    db.refresh(player)

    # Determine if player owns this planet
    is_owned_by_player = planet.owner_id == player.id if planet.owner_id else False

    return LandResponse(
        success=True,
        message=f"Successfully landed on {planet.name}",
        planet_id=str(planet.id),
        planet_name=planet.name,
        planet_type=planet.type.value,
        habitability_score=planet.habitability_score,
        population=planet.population,
        owner_id=str(planet.owner_id) if planet.owner_id else None,
        is_owned_by_player=is_owned_by_player,
        is_population_hub=bool(is_population_hub),
        services=["pioneer_office"] if is_population_hub else [],
    )


@router.post("/leave", response_model=LeaveResponse)
async def leave_planet(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Leave the planet the player is currently on.

    Requirements:
    - Player must be landed on a planet
    """
    # Check if player is landed
    if not player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not currently landed on a planet"
        )

    # Get current planet name for the message
    planet_name = "the planet"
    if player.current_planet_id:
        planet = db.query(Planet).filter(Planet.id == player.current_planet_id).first()
        if planet:
            planet_name = planet.name

    # Perform departure
    player.is_landed = False
    player.current_planet_id = None

    db.commit()
    db.refresh(player)

    return LeaveResponse(
        success=True,
        message=f"Successfully departed from {planet_name}",
        sector_id=player.current_sector_id
    )


# Planet Management Endpoints

@router.get("/owned")
async def get_owned_planets(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get all planets owned by the player."""
    # Lazily complete any genesis planets whose formation timer has elapsed,
    # so a freshly-formed colony shows up usable when the player checks the
    # Colonial Registry (formation completion is lazy poll-on-read).
    try:
        from src.services.genesis_service import GenesisService
        GenesisService(db).complete_due_formations(player.id)
    except Exception:
        logger.exception("Genesis formation sweep failed on owned-planets fetch")

    service = PlanetaryService(db)
    planets = service.get_player_planets(player.id)

    return {
        "planets": planets,
        "totalPlanets": len(planets)
    }


@router.get("/terraforming/levels")
async def get_terraforming_levels(
    player: Player = Depends(get_current_player),
):
    """Get available terraforming levels and their costs."""
    from src.services.terraforming_service import TerraformingService
    return TerraformingService.get_terraforming_levels()


@router.post("/{planet_id}/shields/upgrade")
async def upgrade_shield_generator(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Upgrade the planet's shield generator to the next level."""
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    service = PlanetaryService(db)

    try:
        result = service.upgrade_shield_generator(pid, player.id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{planet_id}/defenses")
async def get_planet_defenses(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get detailed defense information for a planet."""
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    service = PlanetaryService(db)

    try:
        result = service.get_defense_info(pid)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ConstructBuildingRequest(BaseModel):
    """Defense building construction request."""
    buildingType: str = Field(..., pattern="^(orbital_platform|turret_network|scanner_array)$")


@router.get("/{planet_id}/buildings/available")
async def get_available_buildings(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get defense buildings available for construction at current citadel level."""
    from src.services.citadel_service import CitadelService
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    service = CitadelService(db)
    result = service.get_available_buildings(pid)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed"))
    return result


@router.post("/{planet_id}/buildings/construct")
async def construct_defense_building(
    planet_id: str,
    request: ConstructBuildingRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Construct a defense building on a planet."""
    from src.services.citadel_service import CitadelService
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    service = CitadelService(db)
    result = service.build_defense_building(pid, player.id, request.buildingType)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Construction failed"))
    db.commit()
    return result


@router.get("/{planetId}")
async def get_planet_details(
    planetId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific planet."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)
    
    try:
        planet_data = service.get_planet_details(planet_id, player.id)
        return planet_data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{planetId}/allocate")
async def allocate_colonists(
    planetId: str,
    allocation: PlanetResourceAllocation,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Allocate colonists to different production areas."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)
    
    try:
        result = service.allocate_colonists(
            planet_id=planet_id,
            player_id=player.id,
            fuel=allocation.fuel,
            organics=allocation.organics,
            equipment=allocation.equipment
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{planetId}/buildings/upgrade")
async def upgrade_building(
    planetId: str,
    request: BuildingUpgradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Upgrade a building on a planet."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)
    
    try:
        result = service.upgrade_building(
            planet_id=planet_id,
            player_id=player.id,
            building_type=request.buildingType,
            target_level=request.targetLevel
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{planetId}/defenses")
async def update_defenses(
    planetId: str,
    request: DefenseUpdateRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Update planetary defenses."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)
    
    try:
        result = service.update_defenses(
            planet_id=planet_id,
            player_id=player.id,
            turrets=request.turrets,
            shields=request.shields,
            fighters=request.fighters
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/genesis/deploy")
async def deploy_genesis_device_legacy(
    request: GenesisDeployRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """
    Deploy a genesis device to create a new planet (legacy endpoint).

    This endpoint is kept for backward compatibility.
    Use POST /genesis/deploy with the new tiered system instead.
    """
    from src.services.genesis_service import GenesisService

    genesis_service = GenesisService(db)

    try:
        # Parse sector_id: accept both UUID strings and integer sector numbers
        try:
            sector_uuid = UUID(request.sectorId)
            # Look up the integer sector_id from the UUID
            from src.models.sector import Sector
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
            if not sector:
                raise HTTPException(status_code=400, detail="Sector not found")
            sector_num = sector.sector_id
        except ValueError:
            sector_num = int(request.sectorId)

        result = genesis_service.deploy_genesis_device(
            player_id=player.id,
            sector_id=sector_num,
            tier=request.tier,  # basic (1 device) or enhanced (3 devices)
            name=request.planetName,  # honor the player's chosen colony name
            registration=request.registration,  # Colonial Registry visibility
        )
        # Translate the service's snake_case result into the camelCase keys
        # the client reads (genesisDevicesRemaining / deploymentTime / planetId);
        # returning the raw dict left all three undefined client-side.
        return {
            "success": result["success"],
            "planetId": result["planet_id"],
            "planetName": result["planet_name"],
            "planetType": result["planet_type"],
            "genesisDevicesRemaining": result["genesis_devices_remaining"],
            "deploymentTime": result["deployment_seconds"],
            "formationStatus": result["formation_status"],
            # Colonial Registry outcome (FROZEN registry contract)
            "registrationStatus": result["registration_status"],
            "registrationFee": result["registration_fee"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{planetId}/specialize")
async def set_specialization(
    planetId: str,
    request: SpecializationRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Set planet specialization."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)
    
    try:
        result = service.set_specialization(
            planet_id=planet_id,
            player_id=player.id,
            specialization=request.specialization
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{planetId}/siege-status")
async def get_siege_status(
    planetId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get siege status of a planet."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")
    
    service = PlanetaryService(db)

    try:
        result = service.get_siege_status(planet_id, player.id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Citadel Endpoints

class CitadelDepositRequest(BaseModel):
    amount: int = Field(..., gt=0)


class CitadelWithdrawRequest(BaseModel):
    amount: int = Field(..., gt=0)


class CitadelCommodityRequest(BaseModel):
    commodity: str = Field(..., pattern="^(fuel_ore|organics|equipment)$")
    amount: int = Field(..., gt=0)


@router.get("/{planetId}/citadel")
async def get_citadel_info(
    planetId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get citadel information for a planet."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.get_citadel_info(planet_id, player.id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to get citadel info"))
    return result


@router.post("/{planetId}/citadel/upgrade")
async def upgrade_citadel(
    planetId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Start a citadel upgrade on a planet."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.start_upgrade(planet_id, player.id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Citadel upgrade failed"))
    db.commit()
    return result


@router.post("/{planetId}/citadel/cancel")
async def cancel_citadel_upgrade(
    planetId: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Cancel an in-progress citadel upgrade (50% credit refund)."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.cancel_upgrade(planet_id, player.id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Citadel upgrade cancel failed"))
    db.commit()
    return result


@router.post("/{planetId}/citadel/deposit")
async def citadel_deposit(
    planetId: str,
    request: CitadelDepositRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Deposit credits into the citadel's safe storage."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.deposit_to_safe(planet_id, player.id, request.amount)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Deposit failed"))
    db.commit()
    return result


@router.post("/{planetId}/citadel/withdraw")
async def citadel_withdraw(
    planetId: str,
    request: CitadelWithdrawRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Withdraw credits from the citadel's safe storage."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.withdraw_from_safe(planet_id, player.id, request.amount)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Withdrawal failed"))
    db.commit()
    return result


@router.post("/{planetId}/citadel/deposit-commodity")
async def citadel_deposit_commodity(
    planetId: str,
    request: CitadelCommodityRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Move a commodity from the planet stockpile into the protected citadel safe."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.deposit_commodity_to_safe(planet_id, player.id, request.commodity, request.amount)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Deposit failed"))
    db.commit()
    return result


@router.post("/{planetId}/citadel/withdraw-commodity")
async def citadel_withdraw_commodity(
    planetId: str,
    request: CitadelCommodityRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Move a commodity from the citadel safe back onto the planet stockpile."""
    try:
        planet_id = UUID(planetId)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.citadel_service import CitadelService
    service = CitadelService(db)
    result = service.withdraw_commodity_from_safe(planet_id, player.id, request.commodity, request.amount)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Withdrawal failed"))
    db.commit()
    return result


# Terraforming Endpoints

class TerraformingStartRequest(BaseModel):
    """Terraforming project start request."""
    target_level: int = Field(..., ge=1, le=5)


@router.post("/{planet_id}/terraforming/start")
async def start_terraforming(
    planet_id: str,
    request: TerraformingStartRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Start a terraforming project on a planet you own.

    Requirements (enforced by TerraformingService):
    - Player must own the planet (landing is not required — terraforming
      is managed remotely, like colonist allocation)
    - No terraforming project already active on the planet
    - Planet habitability must be below 90%
    - Credits (drawn from the player) and organics + equipment (drawn
      from the planet's stockpile) per the requested level's costs

    Levels 1-5 grant +10 to +30 habitability over 72h-336h.
    """
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.terraforming_service import TerraformingService
    service = TerraformingService(db)

    try:
        return service.start_terraforming(pid, player.id, level=request.target_level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{planet_id}/terraforming/status")
async def get_terraforming_status(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get terraforming status for a planet you own.

    Reading status lazily applies every population-scaled tick accrued
    since the project's last advance (1-3 habitability points per tick,
    tick period derived from the level duration so a < 1,000-population
    planet completes in exactly the documented duration) and completes
    the project once the target habitability is reached.
    """
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    from src.services.terraforming_service import TerraformingService
    service = TerraformingService(db)

    try:
        return service.get_terraforming_status(pid, player.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{planet_id}/terraforming/cancel")
async def cancel_terraforming(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Cancel an active terraforming project on a planet you own.

    Refund semantics (TerraformingService): 50% of the original credit
    cost is returned to the player; consumed planet resources (organics,
    equipment) are NOT refunded.
    """
    try:
        pid = UUID(planet_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid planet ID format")

    # Lock the player row to prevent concurrent credit races on the refund
    # (mirrors the claim route's with_for_update pattern)
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    from src.services.terraforming_service import TerraformingService
    service = TerraformingService(db)

    try:
        return service.cancel_terraforming(pid, player.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))