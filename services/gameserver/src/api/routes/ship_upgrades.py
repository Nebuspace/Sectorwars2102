"""
Ship Upgrades, Equipment & Purchase API Routes

Player-facing endpoints for upgrading ships, managing equipment slots,
and purchasing pre-fabricated ships at shipyard-capable stations.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import ShipSpecification, ShipType, UpgradeType
from src.models.station import Station, StationType
from src.services.ship_service import ShipService
from src.services.ship_upgrade_service import ShipUpgradeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ships", tags=["ship-upgrades"])


# Request/Response Models

class UpgradeRequest(BaseModel):
    ship_id: str
    upgrade_type: str = Field(..., description="One of: ENGINE, CARGO_HOLD, SHIELD, HULL, SENSOR, DRONE_BAY, GENESIS_CONTAINMENT")


class EquipmentRequest(BaseModel):
    ship_id: str
    equipment_key: str = Field(..., description="One of: quantum_harvester, mining_laser, planetary_lander")


class ShipPurchaseRequest(BaseModel):
    ship_type: str = Field(..., description="Ship type to purchase, e.g. LIGHT_FREIGHTER or 'Light Freighter'")
    name: Optional[str] = Field(None, max_length=100, description="Optional custom name for the new ship")


# Helpers

def _station_offers_shipyard(station: Station) -> bool:
    """A station offers shipyard services if it is a SpaceDock, a SHIPYARD-type
    station, or advertises ship sales in its services JSONB."""
    services = station.services or {}
    return (
        bool(station.is_spacedock)
        or station.type == StationType.SHIPYARD
        or bool(services.get("shipyard"))
        or bool(services.get("ship_dealer"))
    )


# Endpoints

@router.get("/catalog")
async def get_ship_catalog(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List ship types and their specifications so the shipyard UI can render
    real catalog data. Viewing does not require being docked at a shipyard."""
    specs = db.query(ShipSpecification).order_by(ShipSpecification.base_cost).all()

    ships = []
    for spec in specs:
        acquisition_methods = spec.acquisition_methods or []
        ships.append({
            "type": spec.type.value,
            "name": spec.type.value.replace("_", " ").title(),
            "base_cost": spec.base_cost,
            "purchasable": "purchase" in acquisition_methods,
            "speed": spec.speed,
            "turn_cost": spec.turn_cost,
            "max_cargo": spec.max_cargo,
            "max_colonists": spec.max_colonists,
            "max_drones": spec.max_drones,
            "max_shields": spec.max_shields,
            "hull_points": spec.hull_points,
            "evasion": spec.evasion,
            "attack_rating": spec.attack_rating,
            "defense_rating": spec.defense_rating,
            "max_genesis_devices": spec.max_genesis_devices,
            "warp_compatible": spec.warp_compatible,
            "description": spec.description,
        })

    return {"ships": ships}


@router.post("/purchase")
async def purchase_ship(
    request: ShipPurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase a pre-fabricated ship at the station the player is docked at.

    Requires the player to be docked at a station offering shipyard services.
    Deducts the specification's base cost from the player's credits and creates
    the ship in the player's current sector. The new ship does NOT replace the
    player's current ship unless they have none.
    """
    # Normalize and validate ship type ("Light Freighter" -> LIGHT_FREIGHTER)
    normalized_type = request.ship_type.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        ship_type = ShipType(normalized_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ship type: {request.ship_type}. Valid types: {[t.value for t in ShipType]}",
        )

    # Lock the player row to prevent concurrent purchases double-spending credits
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Must be docked at a station
    if not player.is_docked or not player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station to purchase a ship",
        )

    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Docked station not found",
        )

    if not _station_offers_shipyard(station):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer shipyard services",
        )

    spec = db.query(ShipSpecification).filter(
        ShipSpecification.type == ship_type
    ).first()
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No specification available for ship type {ship_type.value}",
        )

    # Only ship types flagged as purchasable can be bought at a shipyard
    # (blocks ESCAPE_POD free-dupes and WARP_JUMPER special construction)
    acquisition_methods = spec.acquisition_methods or []
    if "purchase" not in acquisition_methods:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship_type.value.replace('_', ' ').title()} cannot be purchased at a shipyard",
        )

    if player.credits < spec.base_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {spec.base_cost}, have {player.credits}",
        )

    # Deduct credits and create the ship in one transaction
    player.credits -= spec.base_cost

    custom_name = request.name.strip() if request.name and request.name.strip() else None
    ship_service = ShipService(db)
    try:
        ship = ship_service.create_ship(
            ship_type=ship_type,
            owner_id=player.id,
            sector_id=player.current_sector_id,
            name=custom_name,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Only adopt the new ship as current/flagship if the player has none
    if player.current_ship_id is None:
        player.current_ship_id = ship.id
        ship.is_flagship = True
    else:
        ship.is_flagship = False

    db.commit()

    logger.info(
        f"Player {player.id} purchased {ship_type.value} '{ship.name}' "
        f"for {spec.base_cost} credits at station {station.id}"
    )

    return {
        "ship": {
            "id": str(ship.id),
            "name": ship.name,
            "type": ship.type.value,
        },
        "remaining_credits": player.credits,
    }


@router.get("/{ship_id}/upgrades")
async def get_ship_upgrades(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get upgrade and equipment info for a specific ship."""
    service = ShipUpgradeService(db)
    result = service.get_upgrade_info(ship_id, player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Failed to get upgrade info"),
        )
    return result


@router.post("/{ship_id}/upgrades/purchase")
async def purchase_ship_upgrade(
    ship_id: str,
    request: UpgradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase an upgrade for a ship."""
    # Validate upgrade type
    try:
        upgrade_type = UpgradeType(request.upgrade_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid upgrade type: {request.upgrade_type}. Valid types: {[t.value for t in UpgradeType]}",
        )

    service = ShipUpgradeService(db)
    result = service.purchase_upgrade(ship_id, player.id, upgrade_type)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Upgrade purchase failed"),
        )
    db.commit()
    return result


@router.post("/{ship_id}/equipment/install")
async def install_ship_equipment(
    ship_id: str,
    request: EquipmentRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Install equipment into a ship's equipment slot."""
    service = ShipUpgradeService(db)
    result = service.install_equipment(ship_id, player.id, request.equipment_key)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Equipment installation failed"),
        )
    db.commit()
    return result


@router.post("/{ship_id}/equipment/uninstall")
async def uninstall_ship_equipment(
    ship_id: str,
    request: EquipmentRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Uninstall equipment from a ship's equipment slot. No refund."""
    service = ShipUpgradeService(db)
    result = service.uninstall_equipment(ship_id, player.id, request.equipment_key)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Equipment uninstall failed"),
        )
    db.commit()
    return result
