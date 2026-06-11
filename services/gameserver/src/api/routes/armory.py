"""
Armory API Routes

Player-facing endpoints for purchasing combat consumables (attack drones,
defense drones, and mines) at stations offering drone shop or mine dealer
services. Drones and mines live on the Player row (player.attack_drones,
player.defense_drones, player.mines) and are read by combat and the
SpaceDock loadout display.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification
from src.models.station import Station

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/armory", tags=["armory"])


# Canon prices from the FEATURES docs. Each item is gated by a station
# service key in the services JSONB ('drone_shop' for drones, 'mine_dealer'
# for mines); a SpaceDock automatically offers every armory service.
ARMORY_CATALOG = {
    "attack_drone": {
        "name": "Attack Drone",
        "price": 1000,
        "description": "Offensive combat drone. Screens enemy drones first in battle; +5% combat effectiveness per 10 deployed.",
        "service": "drone_shop",
    },
    "defense_drone": {
        "name": "Defense Drone",
        "price": 1200,
        "description": "Defensive escort drone. Reduces incoming damage by 5% per 10 deployed.",
        "service": "drone_shop",
    },
    "limpet_mine": {
        "name": "Limpet Mine",
        "price": 2000,
        "description": "Attaches to passing hulls and signals the owner with the victim's movements.",
        "service": "mine_dealer",
    },
    "armored_mine": {
        "name": "Armored Mine",
        "price": 5000,
        "description": "Hardened proximity mine that detonates against hostile ships entering the sector.",
        "service": "mine_dealer",
    },
}

# ShipSpecification has no mine-capacity column (Ship.max_mines exists but is
# a per-hull value defaulting to 0, not a spec-level cap), so we use the
# documented SpaceDock loadout limit: mines display as 0/25 for a hauler.
MINES_CAP = 25

ArmoryItem = Literal["attack_drone", "defense_drone", "limpet_mine", "armored_mine"]


class ArmoryPurchaseRequest(BaseModel):
    item: ArmoryItem
    quantity: int = Field(..., ge=1, le=100, description="Number of units to purchase (1-100)")


# Helpers

def _station_offers_service(station: Station, service_key: str) -> bool:
    """A station offers an armory service if it is a SpaceDock (which carries
    every service automatically, mirroring _station_offers_shipyard in
    ship_upgrades.py) or advertises the service in its services JSONB."""
    services = station.services or {}
    return bool(station.is_spacedock) or bool(services.get(service_key))


# Endpoints

@router.get("/catalog")
async def get_armory_catalog(
    player: Player = Depends(get_current_player),
):
    """List armory items with prices and the station service gating each.
    Viewing the catalog does not require being docked."""
    return {
        "items": [
            {
                "item": key,
                "name": entry["name"],
                "price": entry["price"],
                "description": entry["description"],
                "service": entry["service"],
            }
            for key, entry in ARMORY_CATALOG.items()
        ]
    }


@router.post("/purchase")
async def purchase_armory_item(
    request: ArmoryPurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase drones or mines at the station the player is docked at.

    Requires the station to offer the item's service (drone_shop or
    mine_dealer; SpaceDocks offer both). Attack and defense drones are each
    capped at the current ship specification's max_drones; mines are capped
    at MINES_CAP. Credits and loadout mutate on the locked player row in a
    single transaction.
    """
    entry = ARMORY_CATALOG[request.item]

    # Lock the player row to prevent concurrent purchases double-spending
    # credits or overshooting loadout caps
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Must be docked at a station
    if not player.is_docked or not player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station to purchase armory items",
        )

    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Docked station not found",
        )

    if not _station_offers_service(station, entry["service"]):
        service_label = "a drone shop" if entry["service"] == "drone_shop" else "a mine dealer"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This station does not offer {service_label}",
        )

    # Loadout caps come from the current ship's specification
    if not player.current_ship_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an active ship to carry armory items",
        )

    ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
    spec = None
    if ship:
        spec = db.query(ShipSpecification).filter(
            ShipSpecification.type == ship.type
        ).first()
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No specification available for your current ship",
        )

    caps = {
        "attack_drones": spec.max_drones,
        "defense_drones": spec.max_drones,
        "mines": MINES_CAP,
    }
    current = {
        "attack_drones": player.attack_drones,
        "defense_drones": player.defense_drones,
        "mines": player.mines,
    }
    slot = {
        "attack_drone": "attack_drones",
        "defense_drone": "defense_drones",
        "limpet_mine": "mines",
        "armored_mine": "mines",
    }[request.item]

    if current[slot] + request.quantity > caps[slot]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Capacity exceeded: {current[slot]}/{caps[slot]} {slot.replace('_', ' ')} "
                f"carried, cannot add {request.quantity}"
            ),
        )

    unit_price = entry["price"]
    total_cost = unit_price * request.quantity
    if player.credits < total_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {total_cost}, have {player.credits}",
        )

    # Deduct credits and add to the loadout in one transaction
    player.credits -= total_cost
    if slot == "attack_drones":
        player.attack_drones += request.quantity
    elif slot == "defense_drones":
        player.defense_drones += request.quantity
    else:
        player.mines += request.quantity

    db.commit()

    logger.info(
        f"Player {player.id} purchased {request.quantity}x {request.item} "
        f"for {total_cost} credits at station {station.id}"
    )

    return {
        "item": request.item,
        "quantity": request.quantity,
        "unit_price": unit_price,
        "total_cost": total_cost,
        "remaining_credits": player.credits,
        "loadout": {
            "attack_drones": player.attack_drones,
            "defense_drones": player.defense_drones,
            "mines": player.mines,
            "caps": caps,
        },
    }
