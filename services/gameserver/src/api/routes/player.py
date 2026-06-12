from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.sector import Sector
from src.models.station import Station
from src.models.warp_tunnel import WarpTunnel
from src.services.movement_service import MovementService
from src.services.ranking_service import RankingService
from src.services.ship_service import ShipService

router = APIRouter(
    prefix="/player",
    tags=["player"],
    responses={404: {"description": "Not found"}},
)

class PlayerStateResponse(BaseModel):
    id: str
    username: str
    credits: int
    turns: int
    max_turns: int = 1000
    current_sector_id: int
    is_docked: bool
    is_landed: bool
    current_port_id: str | None = None
    current_planet_id: str | None = None
    defense_drones: int
    attack_drones: int
    # Optional: a player has no ship until first-login completes
    current_ship_id: str | None = None
    # Optional: set when the player belongs to a team
    team_id: str | None = None

    # Reputation and Ranking
    personal_reputation: int = 0
    reputation_tier: str = "Neutral"
    name_color: str = "#FFFFFF"
    military_rank: str = "Recruit"

class ShipResponse(BaseModel):
    id: str
    name: str
    type: str
    sector_id: int
    cargo: Dict[str, Any]
    cargo_capacity: int
    current_speed: float
    base_speed: float
    combat: Dict[str, Any]
    maintenance: Dict[str, Any]
    is_flagship: bool
    purchase_value: int
    current_value: int
    genesis_devices: int = 0
    max_genesis_devices: int = 0

class RepairShipResponse(BaseModel):
    success: bool
    message: str
    credits_charged: int = 0
    credits_remaining: int = 0
    hull: float = 0
    shields: float = 0
    max_hull: float = 0
    max_shields: float = 0

class SectorResponse(BaseModel):
    id: str
    sector_id: int
    sector_number: int | None = None  # Display number (may differ from sector_id in Central Nexus)
    name: str
    type: str
    region_id: str | None = None
    region_name: str | None = None
    hazard_level: int
    radiation_level: float
    resources: Dict[str, Any]
    players_present: List[Any]
    x_coord: int
    y_coord: int
    z_coord: int

class MoveResponse(BaseModel):
    success: bool
    message: str
    new_sector_id: int = None
    turn_cost: int = 0
    turns_remaining: int = 0
    # The movement service attaches encounter/tunnel events to its result;
    # without these fields response_model filtering silently strips them,
    # which blinds the autopilot's encounter-pause (ADR-0072 Phase 1).
    encounters: list = []
    tunnel_events: list = []

class MoveOption(BaseModel):
    sector_id: int
    sector_number: int | None = None  # Display number
    name: str
    type: str
    region_id: str | None = None
    region_name: str | None = None
    turn_cost: int
    can_afford: bool
    tunnel_type: str = None
    stability: float = None
    # Player warp gates are strictly one-way (tunnel_type "warp_gate",
    # turn_cost 0); natural tunnels report False, direct warps omit it.
    one_way: bool | None = None

class AvailableMovesResponse(BaseModel):
    warps: List[MoveOption]
    tunnels: List[MoveOption]

@router.get("/state", response_model=PlayerStateResponse)
async def get_player_state(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get current player state including credits, turns, ship, and location.

    Also triggers a daily turn refresh if the player's turns have not been
    reset today.  The refresh incorporates both the military-rank bonus and
    the ARIA consciousness multiplier.
    """
    # Check for daily turn refresh (rank bonus + ARIA multiplier applied)
    ranking_service = RankingService(db)
    ranking_service.refresh_daily_turns(player)
    db.commit()

    max_turns = RankingService.calculate_max_turns(player)

    return PlayerStateResponse(
        id=str(player.id),
        username=player.username,
        credits=player.credits,
        turns=player.turns,
        max_turns=max_turns,
        current_sector_id=player.current_sector_id,
        is_docked=player.is_docked,
        is_landed=player.is_landed,
        current_port_id=str(player.current_port_id) if player.current_port_id else None,
        current_planet_id=str(player.current_planet_id) if player.current_planet_id else None,
        defense_drones=player.defense_drones,
        attack_drones=player.attack_drones,
        current_ship_id=str(player.current_ship_id) if player.current_ship_id else None,
        team_id=str(player.team_id) if player.team_id else None,
        personal_reputation=player.personal_reputation,
        reputation_tier=player.reputation_tier,
        name_color=player.name_color,
        military_rank=player.military_rank
    )

@router.get("/ships", response_model=List[ShipResponse])
async def get_player_ships(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get all ships owned by the current player"""
    ships = db.query(Ship).filter(Ship.owner_id == player.id).all()
    
    ship_responses = []
    for ship in ships:
        cargo_data = ship.cargo or {}
        cargo_capacity = cargo_data.get('capacity', 50)
        ship_responses.append(ShipResponse(
            id=str(ship.id),
            name=ship.name,
            type=ship.type.value if hasattr(ship.type, 'value') else str(ship.type),
            sector_id=ship.sector_id,
            cargo=cargo_data,
            cargo_capacity=cargo_capacity,
            current_speed=ship.current_speed,
            base_speed=ship.base_speed,
            combat=ship.combat or {},
            maintenance=ship.maintenance or {},
            is_flagship=ship.is_flagship,
            purchase_value=ship.purchase_value,
            current_value=ship.current_value,
            genesis_devices=ship.genesis_devices or 0,
            max_genesis_devices=ship.max_genesis_devices or 0
        ))

    return ship_responses

@router.get("/current-ship", response_model=ShipResponse)
async def get_current_ship(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get the player's current active ship"""
    if not player.current_ship_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active ship found"
        )
    
    ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
    if not ship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Current ship not found"
        )
    
    cargo_data = ship.cargo or {}
    cargo_capacity = cargo_data.get('capacity', 50)
    return ShipResponse(
        id=str(ship.id),
        name=ship.name,
        type=ship.type.value if hasattr(ship.type, 'value') else str(ship.type),
        sector_id=ship.sector_id,
        cargo=cargo_data,
        cargo_capacity=cargo_capacity,
        current_speed=ship.current_speed,
        base_speed=ship.base_speed,
        combat=ship.combat or {},
        maintenance=ship.maintenance or {},
        is_flagship=ship.is_flagship,
        purchase_value=ship.purchase_value,
        current_value=ship.current_value,
        genesis_devices=ship.genesis_devices or 0,
        max_genesis_devices=ship.max_genesis_devices or 0
    )

@router.post("/ships/{ship_id}/repair", response_model=RepairShipResponse)
async def repair_player_ship(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Repair the player's ship at a docked station offering ship_repair.

    Canon pricing (FEATURES/gameplay/ships.md:84 "Repair options — Basic"):
    "5% of ship value per +10% rating". "Ship value" is the ship's
    current_value; "rating" is the combined hull+shields condition. The
    player must be docked at a station whose services include ship_repair.
    Charges the full restore-to-max cost atomically, then restores hull and
    shields to max via ShipService.repair_ship (Basic = full restore).
    """
    # Lock the player row so the credit charge is race-safe.
    locked_player = db.query(Player).filter(
        Player.id == player.id
    ).with_for_update().first()

    if not locked_player.is_docked or not locked_player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station to repair"
        )

    station = db.query(Station).filter(
        Station.id == locked_player.current_port_id
    ).first()
    if not station:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Docked station not found"
        )

    services = station.services or {}
    if not services.get("ship_repair"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer ship repair services"
        )

    # Lock the ship row; must be the player's own ship.
    ship = db.query(Ship).filter(
        Ship.id == ship_id
    ).with_for_update().first()
    if not ship or ship.owner_id != locked_player.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ship not found"
        )
    if ship.is_destroyed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot repair a destroyed ship"
        )
    if ship.type == ShipType.ESCAPE_POD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escape pods do not require paid repair"
        )

    combat = ship.combat or {}
    max_hull = combat.get("max_hull") or 0
    max_shields = combat.get("max_shields") or 0
    cur_hull = combat.get("hull") or 0
    cur_shields = combat.get("shields") or 0

    # Combined rating deficit as a percentage of the combined max pool. This is
    # the "rating" the canon price is keyed to (hull + shields condition).
    total_max = max_hull + max_shields
    if total_max <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ship has no repairable systems"
        )
    deficit = (max_hull - cur_hull) + (max_shields - cur_shields)
    deficit_pct = max(0.0, (deficit / total_max) * 100.0)

    if deficit_pct <= 0:
        return RepairShipResponse(
            success=True,
            message="Ship is already at full condition",
            credits_charged=0,
            credits_remaining=locked_player.credits,
            hull=cur_hull,
            shields=cur_shields,
            max_hull=max_hull,
            max_shields=max_shields,
        )

    # Basic repair: 5% of current_value per +10% rating restored.
    cost = int(round((ship.current_value or 0) * 0.05 * (deficit_pct / 10.0)))

    if locked_player.credits < cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough credits to repair (need {cost})"
        )

    locked_player.credits -= cost

    # Basic repair fully restores hull/shields (repair_percentage=100).
    ship_service = ShipService(db)
    repair_result = ship_service.repair_ship(ship, repair_percentage=100.0)
    if not repair_result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=repair_result.get("message", "Repair failed")
        )

    db.commit()

    return RepairShipResponse(
        success=True,
        message=f"Ship repaired for {cost} credits",
        credits_charged=cost,
        credits_remaining=locked_player.credits,
        hull=ship.combat.get("hull", max_hull),
        shields=ship.combat.get("shields", max_shields),
        max_hull=max_hull,
        max_shields=max_shields,
    )

@router.get("/current-sector", response_model=SectorResponse)
async def get_current_sector(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get details about the player's current sector"""
    # Filter sector by player's region to prevent cross-regional data leakage
    player_region_id = player.current_region_id

    sector_query = db.query(Sector).filter(Sector.sector_id == player.current_sector_id)
    if player_region_id:
        sector_query = sector_query.filter(Sector.region_id == player_region_id)
    else:
        # For players without region, get sectors with no region
        sector_query = sector_query.filter(Sector.region_id == None)

    sector = sector_query.first()
    if not sector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Current sector not found in your region"
        )

    # Player-facing region label: display_name ("Terran Space"), not the
    # internal import-scoped name ("bang-<uuid>-terran_space")
    region_name = None
    if sector.region:
        region_name = sector.region.display_name or sector.region.name

    return SectorResponse(
        id=str(sector.id),
        sector_id=sector.sector_id,
        sector_number=sector.sector_number if sector.sector_number else sector.sector_id,
        name=sector.name,
        type=sector.type.value if hasattr(sector.type, 'value') else str(sector.type),
        region_id=str(sector.region_id) if sector.region_id else None,
        region_name=region_name,
        hazard_level=sector.hazard_level,
        radiation_level=sector.radiation_level,
        resources=sector.resources or {},
        players_present=sector.players_present or [],
        x_coord=sector.x_coord,
        y_coord=sector.y_coord,
        z_coord=sector.z_coord
    )

@router.post("/move/{sector_id}", response_model=MoveResponse)
async def move_to_sector(
    sector_id: int,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Move the player to a specified sector"""
    # Use MovementService to handle movement properly
    movement_service = MovementService(db)
    result = movement_service.move_player_to_sector(player.id, sector_id)
    
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"]
        )
    
    # Return the movement response with turn cost and remaining turns
    return MoveResponse(
        success=True,
        message=result["message"],
        new_sector_id=sector_id,
        turn_cost=result.get("turn_cost", 0),
        turns_remaining=result.get("turns_remaining", player.turns)
    )

@router.get("/available-moves", response_model=AvailableMovesResponse)
async def get_available_moves(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get available movement options from the player's current sector"""
    # Use MovementService to get properly calculated moves
    movement_service = MovementService(db)
    available_moves = movement_service.get_available_moves(player.id)
    
    # Convert the response to match our model, enriching with region data
    warps = []
    tunnels = []

    # Process direct warps
    for warp in available_moves.get("warps", []):
        # Look up sector to get region information
        sector = db.query(Sector).filter(Sector.sector_id == warp["sector_id"]).first()
        region_name = (sector.region.display_name or sector.region.name) if sector and sector.region else None

        warps.append(MoveOption(
            sector_id=warp["sector_id"],
            sector_number=sector.sector_number if sector and sector.sector_number else warp["sector_id"],
            name=warp["name"],
            type=warp["type"],
            region_id=str(sector.region_id) if sector and sector.region_id else None,
            region_name=region_name,
            turn_cost=warp["turn_cost"],
            can_afford=warp["can_afford"]
        ))

    # Process warp tunnels
    for tunnel in available_moves.get("tunnels", []):
        # Look up sector to get region information
        sector = db.query(Sector).filter(Sector.sector_id == tunnel["sector_id"]).first()
        region_name = (sector.region.display_name or sector.region.name) if sector and sector.region else None

        tunnels.append(MoveOption(
            sector_id=tunnel["sector_id"],
            sector_number=sector.sector_number if sector and sector.sector_number else tunnel["sector_id"],
            name=tunnel["name"],
            type=tunnel["type"],
            region_id=str(sector.region_id) if sector and sector.region_id else None,
            region_name=region_name,
            turn_cost=tunnel["turn_cost"],
            can_afford=tunnel["can_afford"],
            tunnel_type=tunnel.get("tunnel_type"),
            stability=tunnel.get("stability"),
            one_way=tunnel.get("one_way")
        ))

    return AvailableMovesResponse(warps=warps, tunnels=tunnels)


# Genesis Device Purchase
class GenesisPurchaseRequest(BaseModel):
    tier: str  # "standard", "advanced", "experimental"

class GenesisPurchaseResponse(BaseModel):
    success: bool
    message: str
    genesis_devices: int
    max_genesis_devices: int
    new_credits: int
    tier_purchased: str

# Genesis device tiers with pricing
GENESIS_TIERS = {
    "standard": {
        "price": 25000,
        "name": "Standard Genesis Device",
        "success_rate": 0.85,
        "process_hours": 48
    },
    "advanced": {
        "price": 50000,
        "name": "Advanced Genesis Device",
        "success_rate": 0.92,
        "process_hours": 36
    },
    "experimental": {
        "price": 100000,
        "name": "Experimental Genesis Device",
        "success_rate": 0.95,
        "process_hours": 24
    }
}

@router.post("/genesis/purchase", response_model=GenesisPurchaseResponse)
async def purchase_genesis_device(
    request: GenesisPurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Purchase a Genesis Device and add it to the player's ship"""

    # Validate tier
    tier = request.tier.lower()
    if tier not in GENESIS_TIERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid genesis tier. Must be one of: {', '.join(GENESIS_TIERS.keys())}"
        )

    tier_info = GENESIS_TIERS[tier]
    price = tier_info["price"]

    # Lock the player row so the credit charge is race-safe (mirrors
    # repair_player_ship above; populate_existing() refreshes the loaded
    # instance under the lock — trading.py pattern).
    player = (
        db.query(Player)
        .filter(Player.id == player.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")

    # Check if player is docked (required to purchase)
    if not player.is_docked or not player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a SpaceDock to purchase Genesis Devices"
        )

    # Genesis Devices are sold at SpaceDocks (which carry every service) or
    # stations advertising genesis_dealer — same gating rule as
    # _station_offers_service in armory.py / _station_offers_shipyard in
    # ship_upgrades.py.
    station = db.query(Station).filter(
        Station.id == player.current_port_id
    ).first()
    station_services = (station.services or {}) if station else {}
    if not station or not (
        bool(station.is_spacedock) or bool(station_services.get("genesis_dealer"))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a SpaceDock to purchase Genesis Devices"
        )

    # Check credits (under the player row lock)
    if player.credits < price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {price:,}, have {player.credits:,}"
        )

    # Lock the active ship row before reading/mutating genesis capacity
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not ship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active ship found"
        )

    # Check if ship can hold genesis devices
    if ship.max_genesis_devices == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Your {ship.type.value} cannot carry Genesis Devices. You need a Cargo Hauler, Defender, Colony Ship, Carrier, or Warp Jumper."
        )

    # Check if ship has capacity
    current_devices = ship.genesis_devices or 0
    if current_devices >= ship.max_genesis_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Your ship is at maximum Genesis Device capacity ({ship.max_genesis_devices})"
        )

    # Process purchase
    player.credits -= price
    ship.genesis_devices = current_devices + 1

    db.commit()

    return GenesisPurchaseResponse(
        success=True,
        message=f"Successfully purchased {tier_info['name']}!",
        genesis_devices=ship.genesis_devices,
        max_genesis_devices=ship.max_genesis_devices,
        new_credits=player.credits,
        tier_purchased=tier
    )