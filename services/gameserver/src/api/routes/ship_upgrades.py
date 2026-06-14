"""
Ship Upgrades, Equipment & Purchase API Routes

Player-facing endpoints for upgrading ships, managing equipment slots,
and purchasing pre-fabricated ships at shipyard-capable stations.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType, UpgradeType
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


class InsurancePurchaseRequest(BaseModel):
    tier: str = Field(..., description="Insurance tier to buy/upgrade to: BASIC, STANDARD, or PREMIUM")


# Ship insurance (ADR-0081 premium pricing, ADR-0061 payout formula).
# Premium = % of purchase_value paid upfront; net payout on destruction =
# (coverage - deductible)% of purchase_value.
INSURANCE_PREMIUM_PCT = {"BASIC": 0.10, "STANDARD": 0.17, "PREMIUM": 0.22}
INSURANCE_NET_PAYOUT_PCT = {"BASIC": 0.45, "STANDARD": 0.65, "PREMIUM": 0.75}
INSURANCE_TIER_ORDER = ["NONE", "BASIC", "STANDARD", "PREMIUM"]
# Non-insurable hulls: no policy is ever written (ADR-0029).
NON_INSURABLE_TYPES = {ShipType.WARP_JUMPER, ShipType.ESCAPE_POD}


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


def _station_offers_insurance(station: Station) -> bool:
    """A station sells insurance if it advertises the service (SpaceDocks and
    Tier-A/B TradeDocks per bang seeding).

    NOTE: canon (ship-insurance.md) also requires the player to have >= NEUTRAL
    reputation with the station's controlling faction ("friendly port"). That
    refinement is a documented follow-up — no station-service path enforces
    faction standing today and players default to NEUTRAL — so v1 gates on the
    service being offered.
    """
    services = station.services or {}
    return bool(services.get("insurance"))


def _insurance_status(ship: Ship) -> dict:
    """Build the insurance status payload for a ship (current tier + buyable tiers)."""
    pv = ship.purchase_value or 0
    current = (ship.insurance or {}).get("type", "NONE")
    if current not in INSURANCE_TIER_ORDER:
        current = "NONE"
    current_idx = INSURANCE_TIER_ORDER.index(current)
    current_premium = int(pv * INSURANCE_PREMIUM_PCT[current]) if current in INSURANCE_PREMIUM_PCT else 0
    insurable = ship.type not in NON_INSURABLE_TYPES

    tiers = []
    for tier in ("BASIC", "STANDARD", "PREMIUM"):
        tier_idx = INSURANCE_TIER_ORDER.index(tier)
        purchasable = insurable and tier_idx > current_idx
        upgrade_cost = int(pv * INSURANCE_PREMIUM_PCT[tier]) - current_premium if purchasable else None
        tiers.append({
            "tier": tier,
            "premium_pct": INSURANCE_PREMIUM_PCT[tier],
            "premium_full": int(pv * INSURANCE_PREMIUM_PCT[tier]),
            "net_payout_pct": INSURANCE_NET_PAYOUT_PCT[tier],
            "payout_amount": int(pv * INSURANCE_NET_PAYOUT_PCT[tier]),
            "upgrade_cost": upgrade_cost,
            "purchasable": purchasable,
        })

    return {
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.value if ship.type else None,
        "insurable": insurable,
        "current_tier": current,
        "purchase_value": pv,
        "current_payout_amount": int(pv * INSURANCE_NET_PAYOUT_PCT.get(current, 0.0)),
        "tiers": tiers,
    }


# Endpoints

@router.get("/catalog")
async def get_ship_catalog(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List ship types and their specifications so the shipyard UI can render
    real catalog data. Viewing does not require being docked at a shipyard."""
    # NPC-only special-issue hulls (police Interdictors) are never
    # serialized to player-facing ShipType lists (police-forces.md
    # "NPC-only hull classes") — filter at this serializer layer.
    specs = (
        db.query(ShipSpecification)
        .filter(ShipSpecification.is_npc_only == False)  # noqa: E712 — SQLAlchemy boolean comparison
        .order_by(ShipSpecification.base_cost)
        .all()
    )

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
        # NPC-only hulls (NPC_* values) are excluded from the player-facing
        # valid-types list (police-forces.md "NPC-only hull classes").
        valid_types = [t.value for t in ShipType if not t.value.startswith("NPC_")]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ship type: {request.ship_type}. Valid types: {valid_types}",
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

    # NPC-only special-issue hulls can never transfer to player ownership
    # (police-forces.md "Interdictor hulls"; DATA_MODELS/ships.md
    # ERR_NPC_ONLY_HULL)
    if spec.is_npc_only:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"ERR_NPC_ONLY_HULL: {ship_type.value.replace('_', ' ').title()} "
                f"is an NPC-only special-issue hull and cannot be player-owned"
            ),
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


@router.post("/{ship_id}/set-active")
async def set_active_ship(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Make one of the player's ships the active (piloted) ship.

    The client (GameContext.setCurrentShip, the Hangar's MAKE ACTIVE SHIP
    button) has always called this endpoint; it never existed until now.
    The target ship must be in the player's current sector — you walk
    across the hangar, you don't teleport across the galaxy.
    """
    import uuid as _uuid
    try:
        ship_uuid = _uuid.UUID(str(ship_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    ship = db.query(Ship).filter(Ship.id == ship_uuid, Ship.owner_id == player.id).first()
    if not ship:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    if ship.sector_id != locked_player.current_sector_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is in sector {ship.sector_id}; travel there to board it",
        )
    if ship.is_destroyed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is destroyed",
        )
    if ship.status == ShipStatus.HARMONIZING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is harmonizing into a warp gate focus and cannot be boarded",
        )
    if locked_player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lift off before switching ships",
        )

    locked_player.current_ship_id = ship.id
    db.commit()
    return {
        "message": f"{ship.name} is now your active ship",
        "current_ship_id": str(ship.id),
    }


def _resolve_owned_ship(ship_id: str, player: Player, db: Session, lock: bool = False) -> Ship:
    import uuid as _uuid
    try:
        ship_uuid = _uuid.UUID(str(ship_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    q = db.query(Ship).filter(Ship.id == ship_uuid, Ship.owner_id == player.id)
    if lock:
        # Lock the hull row so a concurrent ownership transfer can't race the
        # insurance write (TOCTOU on owner_id).
        q = q.with_for_update()
    ship = q.first()
    if not ship:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    return ship


@router.get("/{ship_id}/insurance")
async def get_ship_insurance(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Current insurance coverage for one of the player's ships plus the buyable
    tiers (premium cost = upgrade difference; net payout per ADR-0061/0081)."""
    ship = _resolve_owned_ship(ship_id, player, db)
    return _insurance_status(ship)


@router.post("/{ship_id}/insurance")
async def purchase_ship_insurance(
    ship_id: str,
    request: InsurancePurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Buy or upgrade insurance on one of the player's ships at a friendly port.

    Premium is paid upfront (ADR-0081); upgrades cost the difference between
    tiers; coverage attaches to the hull for its lifetime. No downgrades, no
    refunds, no claims (ship-insurance.md). Warp Jumpers / Escape Pods are
    non-insurable (ADR-0029).
    """
    tier = request.tier.strip().upper()
    if tier not in INSURANCE_PREMIUM_PCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid insurance tier '{request.tier}'. Valid tiers: BASIC, STANDARD, PREMIUM",
        )

    # Lock the player row first (credits), then resolve + lock the ship row.
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    ship = _resolve_owned_ship(ship_id, locked_player, db, lock=True)

    if ship.is_destroyed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{ship.name} is destroyed")
    if ship.type in NON_INSURABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.type.value.replace('_', ' ').title()} hulls are non-insurable",
        )
    if not ship.purchase_value or ship.purchase_value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} has no insurable value",
        )

    # Must be docked at a station that offers insurance (a friendly port).
    if not locked_player.is_docked or not locked_player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station offering insurance",
        )
    station = db.query(Station).filter(Station.id == locked_player.current_port_id).first()
    if not station or not _station_offers_insurance(station):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer insurance services",
        )

    # No downgrades, no same-tier repurchase; upgrades pay the difference.
    current = (ship.insurance or {}).get("type", "NONE")
    if current not in INSURANCE_TIER_ORDER:
        current = "NONE"
    if INSURANCE_TIER_ORDER.index(tier) <= INSURANCE_TIER_ORDER.index(current):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insurance cannot be downgraded or repurchased at the same tier (current: {current})",
        )

    pv = ship.purchase_value
    current_premium = int(pv * INSURANCE_PREMIUM_PCT[current]) if current in INSURANCE_PREMIUM_PCT else 0
    cost = int(pv * INSURANCE_PREMIUM_PCT[tier]) - current_premium

    # Defense-in-depth: upgrades are always to a strictly higher tier (downgrades
    # rejected above) and premiums are monotonic, so cost is always > 0. Guard
    # anyway so no data anomaly can ever gift credits.
    if cost <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid insurance premium")

    if locked_player.credits < cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits: premium is {cost:,} cr, you have {locked_player.credits:,}",
        )

    locked_player.credits -= cost
    ship.insurance = {"type": tier}
    flag_modified(ship, "insurance")
    db.commit()
    db.refresh(ship)

    status_payload = _insurance_status(ship)
    return {
        "message": f"{ship.name} insured at {tier} ({cost:,} cr)",
        "premium_paid": cost,
        "credits_remaining": locked_player.credits,
        **status_payload,
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
