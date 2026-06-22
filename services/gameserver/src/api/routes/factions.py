"""
Faction API routes for managing faction relationships and missions.
"""

from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.faction import Faction, FactionType, FactionMission
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


class MissionResponse(BaseModel):
    id: str
    faction_id: str
    faction_name: str
    title: str
    description: Optional[str]
    mission_type: str
    credit_reward: int
    item_rewards: List[str]
    target_sector_id: Optional[str]
    cargo_type: Optional[str]
    cargo_quantity: Optional[int]
    expires_at: Optional[datetime]
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_mission(cls, mission: FactionMission) -> "MissionResponse":
        return cls(
            id=str(mission.id),
            faction_id=str(mission.faction_id),
            faction_name=mission.faction.name,
            title=mission.title,
            description=mission.description,
            mission_type=mission.mission_type,
            credit_reward=mission.credit_reward,
            item_rewards=mission.item_rewards or [],
            target_sector_id=str(mission.target_sector_id) if mission.target_sector_id else None,
            cargo_type=mission.cargo_type,
            cargo_quantity=mission.cargo_quantity,
            expires_at=mission.expires_at
        )


class AcceptMissionRequest(BaseModel):
    mission_id: str


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


@router.get("/missions", response_model=List[MissionResponse])
async def get_available_missions(
    faction_id: Optional[UUID] = Query(None, description="Filter by faction ID"),
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get available missions for the current player."""
    service = FactionService(db)
    missions = await service.get_available_missions(current_player.id, faction_id)
    return [MissionResponse.from_mission(mission) for mission in missions]


@router.get("/{faction_id}/missions", response_model=List[MissionResponse])
async def get_faction_missions(
    faction_id: UUID,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get available missions from a specific faction."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    missions = await service.get_available_missions(current_player.id, faction_id)
    return [MissionResponse.from_mission(mission) for mission in missions]


@router.post("/{faction_id}/missions/accept")
async def accept_mission(
    faction_id: UUID,
    request: AcceptMissionRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Accept a mission from a faction."""
    service = FactionService(db)
    
    # Verify faction exists
    faction = await service.get_faction_by_id(faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    
    # Get available missions and check if requested mission is available
    missions = await service.get_available_missions(current_player.id, faction_id)
    mission = next((m for m in missions if str(m.id) == request.mission_id), None)
    
    if not mission:
        raise HTTPException(
            status_code=404, 
            detail="Mission not found or not available to you"
        )
    
    # Lock the player row before mutating settings to prevent concurrent
    # accepts racing past the duplicate/limit checks. with_for_update() does
    # NOT refresh the already-loaded instance, so chain populate_existing()
    # (same pattern as trading.py) and re-read settings from the locked row.
    current_player = (
        db.query(Player)
        .filter(Player.id == current_player.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if current_player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    # Track accepted missions in the player's settings JSONB field.
    # Copy the containers (no in-place mutation): JSONB in-place changes are
    # not change-tracked by SQLAlchemy, so reassign + flag_modified below.
    player_settings = dict(current_player.settings or {})
    accepted_missions = list(player_settings.get("accepted_missions", []))

    # Check if player already accepted this mission
    if any(m.get("mission_id") == request.mission_id for m in accepted_missions):
        raise HTTPException(
            status_code=409,
            detail="You have already accepted this mission"
        )

    # Check max concurrent missions (limit to 5)
    active_missions = [m for m in accepted_missions if m.get("status") == "active"]
    if len(active_missions) >= 5:
        raise HTTPException(
            status_code=400,
            detail="You cannot accept more than 5 missions at a time"
        )

    # Record the accepted mission
    accepted_missions.append({
        "mission_id": request.mission_id,
        "faction_id": str(faction_id),
        "title": mission.title,
        "mission_type": mission.mission_type,
        "status": "active",
        "accepted_at": datetime.utcnow().isoformat(),
        "credit_reward": mission.credit_reward,
        "target_sector_id": str(mission.target_sector_id) if mission.target_sector_id else None,
        "cargo_type": mission.cargo_type,
        "cargo_quantity": mission.cargo_quantity,
        "expires_at": mission.expires_at.isoformat() if mission.expires_at else None
    })

    # Reassign the whole settings dict (never mutate in place) and belt-and-
    # braces flag_modified so SQLAlchemy persists the JSONB change.
    player_settings["accepted_missions"] = accepted_missions
    current_player.settings = player_settings
    flag_modified(current_player, "settings")
    db.commit()

    return {
        "success": True,
        "message": f"Mission '{mission.title}' accepted",
        "mission_id": request.mission_id
    }


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