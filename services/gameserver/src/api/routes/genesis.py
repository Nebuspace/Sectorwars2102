"""
Genesis Device API endpoints.

Handles deploying genesis devices, checking formation status,
and querying available purchase information.
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.genesis_service import GenesisService

router = APIRouter(prefix="/genesis", tags=["genesis"])


# ------------------------------------------------------------------ #
#  Request / Response Models
# ------------------------------------------------------------------ #

class FormationStatusResponse(BaseModel):
    """Response for formation status check."""
    planet_id: str
    planet_name: str
    planet_type: str
    genesis_tier: Optional[str] = None
    formation_status: str
    formation_started_at: Optional[str] = None
    formation_complete_at: Optional[str] = None
    formation_completed_at: Optional[str] = None
    hours_remaining: float
    seconds_remaining: int
    progress_percent: float
    is_usable: bool


class ReputationGate(BaseModel):
    """Federation reputation gate for genesis device acquisition/deploy
    (ADR-0088). Lets the client render the requirement pre-click instead of
    only surfacing it on the 400 the gate raises."""
    required: int
    current: int
    met: bool


class AvailablePurchasesResponse(BaseModel):
    """Response for available genesis device purchases."""
    purchases_this_week: int
    purchases_remaining: int
    max_purchases_per_week: int
    player_credits: int
    current_ship_type: Optional[str] = None
    ship_genesis_capacity: int
    formation_hours: int
    tiers: dict
    reputation_gate: ReputationGate
    # Flat one-time acquisition price for one Genesis Device (distinct from
    # tiers.*.cost, the deploy sequence cost) — DRY-sourced from the same
    # GENESIS_DEVICE_PRICE constant POST /player/genesis/purchase charges.
    device_acquisition_cost: int


class GenesisQuoteResponse(BaseModel):
    """Read-only quote for a (tier, registration) pair, priced for the
    current player's reputation (WO-API-B2). Sourced from the exact same
    compute_genesis_costs the deploy path charges from, so this can never
    drift from the eventual charge. No state is changed by this call."""
    tier: str
    registration: str
    device_cost: int
    registration_fee: int
    total_cost: int
    player_credits: int
    can_afford: bool
    reputation_gate: ReputationGate


# ------------------------------------------------------------------ #
#  Endpoints
# ------------------------------------------------------------------ #

@router.get("/status/{planet_id}", response_model=FormationStatusResponse)
async def get_formation_status(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Check the formation status of a genesis-created planet.

    Returns time remaining, progress percentage, and whether the planet
    is usable. If the formation period has elapsed, the planet is
    automatically transitioned to a usable state.
    """
    try:
        planet_uuid = UUID(planet_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid planet ID format",
        )

    service = GenesisService(db)

    try:
        result = service.check_formation_status(
            planet_id=planet_uuid,
            player_id=player.id,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/available", response_model=AvailablePurchasesResponse)
async def get_available_purchases(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Get information about genesis device availability for the current player.

    Returns:
    - How many devices the player can still purchase this week
    - Cost and requirements for each tier
    - Whether the player can afford each tier
    - Current ship's genesis device capacity
    """
    service = GenesisService(db)

    try:
        result = service.get_available_purchases(player_id=player.id)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/quote", response_model=GenesisQuoteResponse)
async def get_genesis_quote(
    tier: str = Query(
        ...,
        pattern="^(basic|enhanced|advanced)$",
        description="Genesis device tier: basic, enhanced, or advanced",
    ),
    registration: str = Query(
        "registered",
        pattern="^(clandestine|registered|chartered)$",
        description="Colonial Registry visibility: clandestine, registered, or chartered",
    ),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Server-authoritative quote for deploying a genesis device with the given
    tier + Colonial Registry visibility, priced for the current player's
    reputation (the Chartered fee scales with it).

    Read-only: makes no credit, reputation, or device changes. Priced from
    the exact same cost function ``POST /planets/genesis/deploy`` charges
    from, so this quote is guaranteed to equal the eventual charge.
    """
    service = GenesisService(db)

    try:
        result = service.get_genesis_quote(
            player_id=player.id,
            tier=tier,
            registration=registration,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
