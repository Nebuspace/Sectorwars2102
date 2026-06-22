"""
Faction API routes for managing faction relationships and missions.
"""

from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation
from src.models.sector import Sector
from src.services.faction_service import (
    FactionService,
    get_sector_influence,
    sector_territory_tier,
)

router = APIRouter(prefix="/factions", tags=["factions"])


# Pydantic models for API
class FactionResponse(BaseModel):
    id: str
    name: str
    faction_type: str
    description: Optional[str]
    color_primary: Optional[str]
    color_secondary: Optional[str]
    logo_url: Optional[str]
    territory_count: int
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_faction(cls, faction: Faction) -> "FactionResponse":
        return cls(
            id=str(faction.id),
            name=faction.name,
            faction_type=faction.faction_type.value,
            description=faction.description,
            color_primary=faction.color_primary,
            color_secondary=faction.color_secondary,
            logo_url=faction.logo_url,
            territory_count=len(faction.territory_sectors or [])
        )


class ReputationResponse(BaseModel):
    faction_id: str
    faction_name: str
    faction_type: str
    current_value: int
    current_level: str
    title: str
    trade_modifier: float
    port_access_level: int
    combat_response: str
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_reputation(cls, reputation: Reputation) -> "ReputationResponse":
        return cls(
            faction_id=str(reputation.faction_id),
            faction_name=reputation.faction.name,
            faction_type=reputation.faction.faction_type.value,
            current_value=reputation.current_value,
            current_level=reputation.current_level.value,
            title=reputation.title,
            trade_modifier=reputation.trade_modifier,
            port_access_level=reputation.port_access_level,
            combat_response=reputation.combat_response
        )


class TerritoryResponse(BaseModel):
    faction_id: str
    faction_name: str
    sectors: List[str]
    home_sector_id: Optional[str]


class SectorInfluenceEntry(BaseModel):
    """One faction's influence over one sector (ADR-0021)."""
    faction_id: str
    faction_name: Optional[str]
    influence_percentage: float


class SectorInfluenceResponse(BaseModel):
    """Per-sector faction influence + derived territory tier (ADR-0021).

    The READ side of ``SectorFactionInfluence``: surfaces every faction's
    influence over a sector and the four-tier taxonomy classification (Core /
    Controlled / Contested / Uncontrolled). A sector with no influence rows
    reads as ``tier="uncontrolled"`` with an empty list — reproduce-exactly.
    """
    sector_id: int
    sector_uuid: str
    tier: str
    dominant_faction_id: Optional[str]
    influences: List[SectorInfluenceEntry]


# API Endpoints
@router.get("/", response_model=List[FactionResponse])
async def list_factions(
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get list of all factions."""
    service = FactionService(db)
    factions = await service.get_all_factions()
    return [FactionResponse.from_faction(faction) for faction in factions]


@router.get("/reputation", response_model=List[ReputationResponse])
async def get_player_reputations(
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get current player's reputation with all factions."""
    service = FactionService(db)
    reputations = await service.get_all_player_reputations(current_player.id)
    
    # Initialize reputations if none exist
    if not reputations:
        reputations = await service.initialize_player_reputations(current_player.id)
    
    return [ReputationResponse.from_reputation(rep) for rep in reputations]


@router.get("/{faction_id}/reputation", response_model=ReputationResponse)
async def get_faction_reputation(
    faction_id: UUID,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get player's reputation with a specific faction."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    reputation = await service.get_player_reputation(current_player.id, faction_id)
    if not reputation:
        # Initialize if doesn't exist
        await service.initialize_player_reputations(current_player.id)
        reputation = await service.get_player_reputation(current_player.id, faction_id)
    
    return ReputationResponse.from_reputation(reputation)


@router.get("/{faction_id}/territory", response_model=TerritoryResponse)
async def get_faction_territory(
    faction_id: UUID,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get the territory controlled by a faction."""
    service = FactionService(db)
    
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    return TerritoryResponse(
        faction_id=str(faction.id),
        faction_name=faction.name,
        sectors=[str(sid) for sid in (faction.territory_sectors or [])],
        home_sector_id=str(faction.home_sector_id) if faction.home_sector_id else None
    )


@router.get("/sectors/{sector_id}/influence", response_model=SectorInfluenceResponse)
async def get_sector_faction_influence(
    sector_id: int,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """READ per-sector faction influence + territory tier (WO-FI / ADR-0021).

    ``sector_id`` is the GLOBAL human-readable sector number (the integer
    ``sectors.sector_id`` column the rest of the player UI uses), resolved to
    the sector UUID that ``SectorFactionInfluence`` keys on. A sector with no
    influence rows returns ``tier="uncontrolled"`` with an empty list — the
    pre-existing, reproduce-exactly behavior.
    """
    sector = (
        db.query(Sector)
        .filter(Sector.sector_id == sector_id)
        .first()
    )
    if sector is None:
        raise HTTPException(status_code=404, detail="Sector not found")

    rows = get_sector_influence(db, sector.id)
    tier = sector_territory_tier(rows)

    # Resolve faction names in one batched query (avoid per-row lazy loads).
    faction_ids = [row.faction_id for row in rows]
    name_by_id = {}
    if faction_ids:
        name_by_id = {
            f.id: f.name
            for f in db.query(Faction.id, Faction.name)
            .filter(Faction.id.in_(faction_ids))
            .all()
        }

    influences = [
        SectorInfluenceEntry(
            faction_id=str(row.faction_id),
            faction_name=name_by_id.get(row.faction_id),
            influence_percentage=row.influence_percentage or 0.0,
        )
        for row in rows
    ]
    dominant = (
        str(rows[0].faction_id)
        if rows and (rows[0].influence_percentage or 0.0) > 0.0
        else None
    )

    return SectorInfluenceResponse(
        sector_id=sector.sector_id,
        sector_uuid=str(sector.id),
        tier=tier,
        dominant_faction_id=dominant,
        influences=influences,
    )


@router.get("/{faction_id}/pricing-modifier")
async def get_pricing_modifier(
    faction_id: UUID,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get the pricing modifier for trading at faction-controlled ports."""
    service = FactionService(db)
    
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    modifier = await service.get_faction_pricing_modifier(current_player.id, faction_id)
    
    return {
        "faction_id": str(faction_id),
        "faction_name": faction.name,
        "base_modifier": faction.base_pricing_modifier,
        "player_modifier": modifier,
        "description": f"{'Discount' if modifier < 1.0 else 'Markup'}: {abs(1.0 - modifier) * 100:.0f}%"
    }