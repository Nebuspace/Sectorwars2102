from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel
from src.services.movement_service import MovementService
from src.services.ranking_service import RankingService

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

    # Get region name if sector belongs to a region
    region_name = None
    if sector.region:
        region_name = sector.region.name

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
        region_name = sector.region.name if sector and sector.region else None

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
        region_name = sector.region.name if sector and sector.region else None

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
            stability=tunnel.get("stability")
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

    # Check if player is docked (required to purchase)
    if not player.is_docked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a SpaceDock to purchase Genesis Devices"
        )

    # Check credits
    if player.credits < price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {price:,}, have {player.credits:,}"
        )

    # Get current ship
    ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
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