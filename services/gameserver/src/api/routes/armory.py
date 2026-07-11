"""
Armory API Routes

Player-facing endpoints for purchasing combat consumables (attack drones,
defense drones, and mines) at stations offering drone shop or mine dealer
services. Drones and mines live on the Player row (player.attack_drones,
player.defense_drones, player.mines) and are read by combat and the
SpaceDock loadout display. Carried-scalar caps are the ship spec's
max_drones plus the installed Drone Bay upgrade's +2/level bonus
(DroneService._drone_bay_bonus) — the same bonus the drone-ROWS economy
applies (drone_service.py _get_max_drones), so a Drone-Bay-upgraded ship's
armory purchase cap agrees with its deployable-drone cap.
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
from src.services.drone_service import DroneService

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


def _armory_caps(ship: Ship, spec: ShipSpecification) -> dict:
    """Attack/defense drone + mine purchase caps for a given ship + spec.

    Single source of truth for the caps formula — both GET /catalog and
    POST /purchase call this so the two never drift apart. Carried-scalar
    caps agree with the drone-ROWS economy's cap (drone_service.py
    _get_max_drones): spec.max_drones + the installed Drone Bay upgrade's
    +2/level bonus (DroneService._drone_bay_bonus); mines are capped at
    MINES_CAP.
    """
    drone_bay_bonus = DroneService._drone_bay_bonus(ship)
    return {
        "attack_drones": spec.max_drones + drone_bay_bonus,
        "defense_drones": spec.max_drones + drone_bay_bonus,
        "mines": MINES_CAP,
    }


def _current_loadout(player: Player, db: Session) -> dict | None:
    """Compute the player's carried drone/mine counts and purchase caps from
    their current ship + specification.

    Returns None when the player has no current ship or no resolvable
    specification — caps are undefined in that state. Callers must OMIT the
    loadout key entirely rather than surface it with caps: null, since the
    SpaceDock UI dereferences loadout.caps.* unconditionally once a loadout
    key is present (a null caps object would crash the frontend).
    """
    if not player.current_ship_id:
        return None
    ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
    if not ship:
        return None
    spec = db.query(ShipSpecification).filter(
        ShipSpecification.type == ship.type
    ).first()
    if not spec:
        return None
    return {
        "attack_drones": player.attack_drones,
        "defense_drones": player.defense_drones,
        "mines": player.mines,
        "caps": _armory_caps(ship, spec),
    }


# Endpoints

@router.get("/catalog")
async def get_armory_catalog(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List armory items with prices and the station service gating each.
    Viewing the catalog does not require being docked.

    Includes the player's current loadout + purchase caps (via
    _current_loadout) when they have an active ship with a resolvable
    specification, so the SpaceDock loadout box has caps to render on
    entry instead of waiting for a purchase response. Omitted entirely for
    a shipless player — see _current_loadout for why caps: null is not an
    option here.
    """
    response = {
        "items": [
            {
                "item": key,
                "name": entry["name"],
                "price": entry["price"],
                "description": entry["description"],
                "service": entry["service"],
                # Armored mines are deployable (proximity detonation on hostile
                # sector entry — POST /armory/deploy). Limpet mines need a
                # tracking/surveillance mechanic that isn't built yet, so they
                # stay flagged unavailable rather than sold as a no-op.
                "available": key != "limpet_mine",
                "reason": (
                    "Limpet tracking mechanic is in design"
                    if key == "limpet_mine" else None
                ),
            }
            for key, entry in ARMORY_CATALOG.items()
        ]
    }
    loadout = _current_loadout(player, db)
    if loadout is not None:
        response["loadout"] = loadout
    return response


@router.post("/purchase")
async def purchase_armory_item(
    request: ArmoryPurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase drones or mines at the station the player is docked at.

    Requires the station to offer the item's service (drone_shop or
    mine_dealer; SpaceDocks offer both). Attack and defense drones are each
    capped at the current ship specification's max_drones plus the ship's
    Drone Bay upgrade bonus (+2/level, DroneService._drone_bay_bonus); mines
    are capped at MINES_CAP. Credits and loadout mutate on the locked player
    row in a single transaction.
    """
    entry = ARMORY_CATALOG[request.item]

    # Armored mines are now deployable (POST /armory/deploy). Limpet mines still
    # have no tracking mechanic, so selling one would burn credits for a no-op.
    if request.item == "limpet_mine":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Limpet mines aren't deployable yet — their tracking mechanic is still in design.",
        )

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

    # Same formula source as GET /catalog (_armory_caps) — ship + spec are
    # already loaded locally here, so we call the caps-only helper directly
    # rather than _current_loadout(player, db), which would re-query both.
    caps = _armory_caps(ship, spec)
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


class MineDeployRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=MINES_CAP, description="Number of armored mines to lay in the current sector")


@router.post("/deploy")
async def deploy_mines(
    request: MineDeployRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Lay armored mines in the player's current sector (open space only).

    Deployed mines detonate against the next hostile ship to enter the sector
    (MovementService._detonate_sector_mines). A sector holds one commander's
    minefield at a time; a player can reinforce their own field but not stack on
    a rival's.
    """
    from src.models.sector import Sector

    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    if player.is_docked or player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mines are laid in open space — undock / lift off first.",
        )
    if not player.current_sector_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You are not in a sector")
    if player.mines < request.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You carry {player.mines} mines, cannot deploy {request.quantity}.",
        )

    sector = (
        db.query(Sector)
        .filter(Sector.sector_id == player.current_sector_id)
        .with_for_update()
        .first()
    )
    if not sector:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sector not found")

    defenses = dict(sector.defenses or {})
    existing = int(defenses.get("mines", 0) or 0)
    existing_owner = defenses.get("mine_owner_id")
    if existing > 0 and existing_owner and str(existing_owner) != str(player.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sector already holds another commander's minefield.",
        )

    from sqlalchemy.orm.attributes import flag_modified
    defenses["mines"] = existing + request.quantity
    defenses["mine_owner_id"] = str(player.id)
    defenses["mine_team_id"] = str(player.team_id) if player.team_id else None
    sector.defenses = defenses
    flag_modified(sector, "defenses")
    player.mines -= request.quantity
    db.commit()

    logger.info(
        f"Player {player.id} deployed {request.quantity} mines in sector {player.current_sector_id} "
        f"(field now {defenses['mines']})"
    )
    return {
        "success": True,
        "message": f"Laid {request.quantity} armored mine(s). Sector field: {defenses['mines']}.",
        "sector_id": player.current_sector_id,
        "sector_mines": defenses["mines"],
        "mines_remaining": player.mines,
    }
