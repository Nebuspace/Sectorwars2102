"""
Genesis Device API endpoints.

Handles deploying genesis devices, checking formation status,
and querying available purchase information.
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.genesis_service import GenesisService

router = APIRouter(prefix="/genesis", tags=["genesis"])


# ------------------------------------------------------------------ #
#  Request / Response Models
# ------------------------------------------------------------------ #

class GenesisDeployRequest(BaseModel):
    """Request to deploy a genesis device."""
    sector_id: int = Field(..., description="Target sector number")
    tier: str = Field(
        ...,
        pattern="^(basic|enhanced|advanced)$",
        description="Genesis device tier: basic, enhanced, or advanced",
    )


class GenesisDeployResponse(BaseModel):
    """Response from deploying a genesis device."""
    success: bool
    planet_id: str
    planet_name: str
    planet_type: str
    genesis_tier: str
    habitability_score: int
    resource_richness: float
    size: int
    formation_status: str
    formation_started_at: str
    formation_complete_at: str
    formation_hours_remaining: float
    credits_spent: int
    credits_remaining: int
    genesis_purchases_this_week: int
    genesis_purchases_remaining: int
    ship_sacrificed: Optional[dict] = None


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


# ------------------------------------------------------------------ #
#  Endpoints
# ------------------------------------------------------------------ #

@router.post("/deploy", response_model=GenesisDeployResponse)
async def deploy_genesis_device(
    request: GenesisDeployRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """
    Deploy a genesis device to create a new planet in the specified sector.

    The player must:
    - Be in the target sector
    - Have sufficient credits for the chosen tier
    - Not have exceeded the weekly purchase limit (3 per week)
    - Not be docked at a station or landed on a planet

    For the **advanced** tier, the player's current colony ship is sacrificed.

    The newly created planet enters a "forming" state and becomes usable
    after the formation period (default 48 hours).
    """
    service = GenesisService(db)

    try:
        result = service.deploy_genesis_device(
            player_id=player.id,
            sector_id=request.sector_id,
            tier=request.tier,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


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
