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
from src.auth.dependencies import get_current_admin_user
from src.models.user import User
from src.models.faction import Faction, FactionType, FactionMission
from src.services.faction_service import FactionService

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


class MissionCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str
    mission_type: str = Field(..., description="cargo_delivery, combat, exploration, etc.")
    credit_reward: int = Field(..., ge=0)
    min_reputation: int = Field(default=-800, ge=-800, le=800)
    min_level: int = Field(default=1, ge=1)
    item_rewards: List[str] = Field(default_factory=list)
    target_sector_id: Optional[str] = None
    cargo_type: Optional[str] = None
    cargo_quantity: Optional[int] = Field(None, ge=1)
    target_faction_id: Optional[str] = None
    expires_at: Optional[datetime] = None


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
    admin_user: User = Depends(get_current_admin_user)
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
    admin_user: User = Depends(get_current_admin_user)
):
    """Create a new faction (admin only)."""
    # Check if faction with same name exists
    existing = db.query(Faction).filter(Faction.name == request.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Faction with this name already exists")
    
    faction = Faction(
        name=request.name,
        faction_type=request.faction_type.value if hasattr(request.faction_type, 'value') else request.faction_type,
        description=request.description,
        base_pricing_modifier=request.base_pricing_modifier,
        trade_specialties=request.trade_specialties,
        aggression_level=request.aggression_level,
        diplomacy_stance=request.diplomacy_stance,
        color_primary=request.color_primary,
        color_secondary=request.color_secondary,
        logo_url=request.logo_url
    )
    
    db.add(faction)
    db.commit()
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
        updated_at=faction.updated_at
    )


@router.put("/{faction_id}", response_model=FactionDetailResponse)
async def update_faction(
    faction_id: UUID,
    request: FactionUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Update a faction (admin only)."""
    service = FactionService(db)
    faction = await service.get_faction_by_id(faction_id)
    
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    # Update fields if provided
    update_data = request.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(faction, field, value)
    
    faction.updated_at = datetime.utcnow()
    db.commit()
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
        updated_at=faction.updated_at
    )


@router.delete("/{faction_id}")
async def delete_faction(
    faction_id: UUID,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Delete a faction (admin only)."""
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
        "Colonial Defense Force"
    ]
    
    if faction.name in core_faction_names:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete core game factions"
        )
    
    db.delete(faction)
    db.commit()
    
    return {"success": True, "message": f"Faction '{faction.name}' deleted"}


@router.put("/{faction_id}/territory")
async def update_faction_territory(
    faction_id: UUID,
    request: TerritoryUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Update faction territory control (admin only)."""
    service = FactionService(db)
    
    # Convert string UUIDs to UUID objects
    sector_ids = [UUID(sid) for sid in request.sector_ids]
    
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


@router.post("/{faction_id}/missions", response_model=dict)
async def create_faction_mission(
    faction_id: UUID,
    request: MissionCreateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Create a new mission for a faction (admin only)."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    # Create mission
    mission = await service.create_mission(
        faction_id=faction_id,
        title=request.title,
        description=request.description,
        mission_type=request.mission_type,
        credit_reward=request.credit_reward,
        # ADR-0090: missions no longer promise reputation; persist the neutral
        # default (0) into the still-required service param (service is out of scope).
        reputation_reward=0,
        min_reputation=request.min_reputation,
        min_level=request.min_level,
        item_rewards=request.item_rewards,
        target_sector_id=UUID(request.target_sector_id) if request.target_sector_id else None,
        cargo_type=request.cargo_type,
        cargo_quantity=request.cargo_quantity,
        target_faction_id=UUID(request.target_faction_id) if request.target_faction_id else None,
        expires_at=request.expires_at
    )
    
    return {
        "success": True,
        "mission_id": str(mission.id),
        "title": mission.title,
        "faction_name": faction.name
    }


@router.put("/{faction_id}/reputation")
async def update_player_reputation(
    faction_id: UUID,
    request: ReputationUpdateRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Update a player's reputation with a faction (admin only)."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    # Update reputation
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


@router.get("/missions/all")
async def list_all_missions(
    active_only: bool = True,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user)
):
    """Get all missions across all factions (admin only)."""
    query = db.query(FactionMission).join(Faction)
    
    if active_only:
        query = query.filter(FactionMission.is_active == 1)
    
    missions = query.all()
    
    return [
        {
            "id": str(mission.id),
            "faction_id": str(mission.faction_id),
            "faction_name": mission.faction.name,
            "title": mission.title,
            "mission_type": mission.mission_type,
            "credit_reward": mission.credit_reward,
            "min_reputation": mission.min_reputation,
            "is_active": bool(mission.is_active),
            "expires_at": mission.expires_at,
            "created_at": mission.created_at
        }
        for mission in missions
    ]