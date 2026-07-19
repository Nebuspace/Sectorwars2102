"""
Admin API routes for managing factions.
"""

from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from datetime import datetime

from src.core.database import get_db
from src.auth.admin_scopes import GALAXY_MANAGE, PLAYERS_ADJUST_REP, PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.models.faction import Faction, FactionType
from src.services.faction_service import FactionService
from src.services.admin_action_log_service import log_admin_action
from src.services.admin_action_attempt import admin_action_attempt

router = APIRouter(prefix="/admin/factions", tags=["admin-factions"])


# Admin-specific Pydantic models
class FactionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    faction_type: FactionType
    description: Optional[str] = None
    base_pricing_modifier: float = Field(default=1.0, ge=0.5, le=2.0)
    trade_specialties: List[str] = Field(default_factory=list)
    aggression_level: int = Field(default=5, ge=1, le=10)
    diplomacy_stance: str = Field(default="neutral")
    color_primary: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    color_secondary: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    logo_url: Optional[str] = None


class FactionUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    base_pricing_modifier: Optional[float] = Field(None, ge=0.5, le=2.0)
    trade_specialties: Optional[List[str]] = None
    aggression_level: Optional[int] = Field(None, ge=1, le=10)
    diplomacy_stance: Optional[str] = None
    color_primary: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    color_secondary: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    logo_url: Optional[str] = None


class TerritoryUpdateRequest(BaseModel):
    sector_ids: List[str]
    home_sector_id: Optional[str] = None


class ReputationUpdateRequest(BaseModel):
    player_id: str
    change: int = Field(..., ge=-100, le=100)
    reason: str = Field(default="Admin adjustment")


class FactionDetailResponse(BaseModel):
    id: str
    name: str
    faction_type: str
    description: Optional[str]
    territory_sectors: List[str]
    home_sector_id: Optional[str]
    base_pricing_modifier: float
    trade_specialties: List[str]
    aggression_level: int
    diplomacy_stance: str
    color_primary: Optional[str]
    color_secondary: Optional[str]
    logo_url: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# Admin Endpoints
@router.get("/", response_model=List[FactionDetailResponse])
async def list_all_factions(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(PLAYERS_VIEW))
):
    """Get detailed list of all factions (admin only)."""
    service = FactionService(db)
    factions = await service.get_all_factions()
    
    return [
        FactionDetailResponse(
            id=str(faction.id),
            name=faction.name,
            faction_type=faction.faction_type.value,
            description=faction.description,
            territory_sectors=[str(sid) for sid in (faction.territory_sectors or [])],
            home_sector_id=str(faction.home_sector_id) if faction.home_sector_id else None,
            base_pricing_modifier=faction.base_pricing_modifier,
            trade_specialties=faction.trade_specialties or [],
            aggression_level=faction.aggression_level,
            diplomacy_stance=faction.diplomacy_stance,
            color_primary=faction.color_primary,
            color_secondary=faction.color_secondary,
            logo_url=faction.logo_url,
            created_at=faction.created_at,
            updated_at=faction.updated_at
        )
        for faction in factions
    ]


@router.post("/", response_model=FactionDetailResponse)
async def create_faction(
    request: FactionCreateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(GALAXY_MANAGE))
):
    """Create a new faction (admin only)."""
    with admin_action_attempt(
        db,
        actor=admin_user,
        scope_used=GALAXY_MANAGE,
        action="faction_create",
        target_type="faction",
        target_id="pending",
        payload={"name": request.name},
    ) as attempt:
        existing = db.query(Faction).filter(Faction.name == request.name).first()
        if existing:
            raise HTTPException(
                status_code=400, detail="Faction with this name already exists"
            )

        faction = Faction(
            name=request.name,
            faction_type=(
                request.faction_type.value
                if hasattr(request.faction_type, "value")
                else request.faction_type
            ),
            description=request.description,
            base_pricing_modifier=request.base_pricing_modifier,
            trade_specialties=request.trade_specialties,
            aggression_level=request.aggression_level,
            diplomacy_stance=request.diplomacy_stance,
            color_primary=request.color_primary,
            color_secondary=request.color_secondary,
            logo_url=request.logo_url,
        )

        db.add(faction)
        db.flush()
        attempt.target_id = str(faction.id)
        attempt.succeed(payload={"name": request.name})
        db.refresh(faction)

        return FactionDetailResponse(
            id=str(faction.id),
            name=faction.name,
            faction_type=faction.faction_type.value,
            description=faction.description,
            territory_sectors=[],
            home_sector_id=None,
            base_pricing_modifier=faction.base_pricing_modifier,
            trade_specialties=faction.trade_specialties or [],
            aggression_level=faction.aggression_level,
            diplomacy_stance=faction.diplomacy_stance,
            color_primary=faction.color_primary,
            color_secondary=faction.color_secondary,
            logo_url=faction.logo_url,
            created_at=faction.created_at,
            updated_at=faction.updated_at,
        )


@router.put("/{faction_id}", response_model=FactionDetailResponse)
async def update_faction(
    faction_id: UUID,
    request: FactionUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(GALAXY_MANAGE))
):
    """Update a faction (admin only)."""
    with admin_action_attempt(
        db,
        actor=admin_user,
        scope_used=GALAXY_MANAGE,
        action="faction_update",
        target_type="faction",
        target_id=str(faction_id),
    ) as attempt:
        service = FactionService(db)
        faction = await service.get_faction_by_id(faction_id)

        if not faction:
            raise HTTPException(status_code=404, detail="Faction not found")

        update_data = request.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(faction, field, value)

        faction.updated_at = datetime.utcnow()
        attempt.succeed(payload=update_data)
        db.refresh(faction)

        return FactionDetailResponse(
            id=str(faction.id),
            name=faction.name,
            faction_type=faction.faction_type.value,
            description=faction.description,
            territory_sectors=[str(sid) for sid in (faction.territory_sectors or [])],
            home_sector_id=str(faction.home_sector_id) if faction.home_sector_id else None,
            base_pricing_modifier=faction.base_pricing_modifier,
            trade_specialties=faction.trade_specialties or [],
            aggression_level=faction.aggression_level,
            diplomacy_stance=faction.diplomacy_stance,
            color_primary=faction.color_primary,
            color_secondary=faction.color_secondary,
            logo_url=faction.logo_url,
            created_at=faction.created_at,
            updated_at=faction.updated_at,
        )


@router.delete("/{faction_id}")
async def delete_faction(
    faction_id: UUID,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(GALAXY_MANAGE))
):
    """Delete a faction (admin only)."""
    with admin_action_attempt(
        db,
        actor=admin_user,
        scope_used=GALAXY_MANAGE,
        action="faction_delete",
        target_type="faction",
        target_id=str(faction_id),
    ) as attempt:
        service = FactionService(db)
        faction = await service.get_faction_by_id(faction_id)

        if not faction:
            raise HTTPException(status_code=404, detail="Faction not found")

        # Don't allow deletion of core factions
        core_faction_names = [
            "United Space Federation",
            "Independent Traders Alliance",
            "Shadow Syndicate",
            "Merchant Guild",
            "Stellar Cartographers",
            "Colonial Defense Force",
        ]

        if faction.name in core_faction_names:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete core game factions",
            )

        name = faction.name
        db.delete(faction)
        attempt.succeed(payload={"name": name})

        return {"success": True, "message": f"Faction '{name}' deleted"}


@router.put("/{faction_id}/territory")
async def update_faction_territory(
    faction_id: UUID,
    request: TerritoryUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(GALAXY_MANAGE))
):
    """Update faction territory control (admin only)."""
    service = FactionService(db)
    
    # Convert string UUIDs to UUID objects
    sector_ids = [UUID(sid) for sid in request.sector_ids]
    
    log_admin_action(
        db,
        actor=admin_user,
        scope_used=GALAXY_MANAGE,
        action="faction_territory_update",
        target_type="faction",
        target_id=str(faction_id),
        payload={"sector_count": len(request.sector_ids)},
    )

    faction = await service.update_faction_territory(faction_id, sector_ids)
    
    if request.home_sector_id:
        faction.home_sector_id = UUID(request.home_sector_id)
        db.commit()
    
    return {
        "success": True,
        "faction_id": str(faction_id),
        "faction_name": faction.name,
        "territory_count": len(sector_ids),
        "home_sector_id": str(faction.home_sector_id) if faction.home_sector_id else None
    }


@router.put("/{faction_id}/reputation")
async def update_player_reputation(
    faction_id: UUID,
    request: ReputationUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_scope(PLAYERS_ADJUST_REP))
):
    """Update a player's reputation with a faction (admin only)."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    # Update reputation
    log_admin_action(
        db,
        actor=admin_user,
        scope_used=PLAYERS_ADJUST_REP,
        action="faction_reputation_update",
        target_type="player",
        target_id=str(request.player_id),
        payload={"faction_id": str(faction_id), "change": request.change},
    )

    reputation = await service.update_reputation(
        player_id=UUID(request.player_id),
        faction_id=faction_id,
        change=request.change,
        reason=request.reason
    )
    
    return {
        "success": True,
        "player_id": request.player_id,
        "faction_name": faction.name,
        "old_value": reputation.current_value - request.change,
        "new_value": reputation.current_value,
        "new_level": reputation.current_level.value,
        "new_title": reputation.title
    }