"""
Comprehensive Admin API Routes
Supports full game administration based on DOCS specifications
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text, func, desc, and_, or_
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone
import logging
import uuid

from src.core.database import get_db
from src.auth.dependencies import get_current_admin
from src.models.user import User
from src.models.player import Player
from src.models.ship import Ship
from src.models.planet import Planet
from src.models.station import Station, StationStatus
from src.models.sector import Sector
from src.models.cluster import Cluster
from src.models.galaxy import Galaxy
from src.models.region import Region
from src.models.zone import Zone
from src.models.warp_tunnel import WarpTunnel
from src.models.team import Team
from src.services.analytics_service import AnalyticsService
from src.services.ai_security_service import get_security_service

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic Models for Requests/Responses

class PlayerManagementResponse(BaseModel):
    id: str
    username: str
    email: str
    credits: int
    turns: int
    current_sector_id: Optional[int]
    current_ship_id: Optional[str]
    team_id: Optional[str]
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    ships_count: int
    planets_count: int
    ports_count: int

class PlayerUpdateRequest(BaseModel):
    credits: Optional[int] = None
    turns: Optional[int] = None
    is_active: Optional[bool] = None
    reputation_adjustments: Optional[Dict[str, int]] = None

class ShipManagementResponse(BaseModel):
    id: str
    name: str
    ship_type: str
    owner_id: str
    owner_name: str
    current_sector_id: int
    maintenance_rating: float
    cargo_used: int
    cargo_capacity: int
    is_active: bool
    created_at: datetime

class SectorManagementResponse(BaseModel):
    id: str
    sector_id: int
    name: str
    type: str
    cluster_id: str
    region_name: str
    zone_id: Optional[str]
    zone_name: Optional[str]
    zone_type: Optional[str]
    x_coord: int
    y_coord: int
    z_coord: int
    hazard_level: float
    is_discovered: bool
    has_port: bool
    has_planet: bool
    has_warp_tunnel: bool
    player_count: int
    controlling_faction: Optional[str]

class StationManagementResponse(BaseModel):
    id: str
    name: str
    sector_id: str  # Changed to string to match frontend
    sector_name: Optional[str]
    port_class: str
    trade_volume: int
    max_capacity: int
    security_level: int
    docking_fee: int
    owner_id: Optional[str]
    owner_name: Optional[str]
    created_at: str
    is_operational: bool
    commodities: List[str]

class SectorUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    type: Optional[str] = None  # Will validate against SectorType enum
    description: Optional[str] = None
    x_coord: Optional[int] = None
    y_coord: Optional[int] = None
    z_coord: Optional[int] = None
    radiation_level: Optional[float] = Field(None, ge=0.0)
    hazard_level: Optional[int] = Field(None, ge=0, le=10)
    resource_regeneration: Optional[float] = Field(None, ge=0.0)
    is_discovered: Optional[bool] = None
    discovered_by_id: Optional[str] = None
    resources: Optional[Dict[str, Any]] = None
    defenses: Optional[Dict[str, Any]] = None
    controlling_faction: Optional[str] = None
    controlling_team_id: Optional[str] = None
    special_features: Optional[List[str]] = None
    active_events: Optional[List[Dict[str, Any]]] = None
    nav_hazards: Optional[Dict[str, Any]] = None
    nav_beacons: Optional[List[Dict[str, Any]]] = None

class PlanetCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    type: str  # PlanetType enum
    size: Optional[int] = Field(5, ge=1, le=10)
    position: Optional[int] = Field(3, ge=1, le=10)
    gravity: Optional[float] = Field(1.0, ge=0.1, le=10.0)
    temperature: Optional[float] = Field(0.0, ge=-100.0, le=1000.0)
    water_coverage: Optional[float] = Field(0.0, ge=0.0, le=100.0)
    habitability_score: Optional[int] = Field(50, ge=0, le=100)
    resource_richness: Optional[float] = Field(1.0, ge=0.0, le=3.0)

class StationCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    station_class: int = Field(..., ge=0, le=11)  # PortClass enum values
    type: str  # PortType enum
    size: Optional[int] = Field(5, ge=1, le=10)
    faction_affiliation: Optional[str] = None
    trade_volume: Optional[int] = Field(100, ge=0)
    market_volatility: Optional[int] = Field(50, ge=0, le=100)

class PlanetManagementResponse(BaseModel):
    id: str
    name: str
    sector_id: int
    planet_type: str
    owner_id: Optional[str]
    owner_name: Optional[str]
    population: int
    max_population: int
    habitability_score: int
    resource_richness: float
    defense_level: int
    colonized_at: Optional[datetime]
    genesis_created: bool

class CombatLogResponse(BaseModel):
    id: str
    timestamp: datetime
    combat_type: str
    combat_result: str
    sector_id: int
    attacker_id: str
    attacker_name: str
    defender_id: Optional[str]
    defender_name: Optional[str]
    turns_consumed: int
    combat_rounds: int
    attacker_ship_destroyed: bool
    defender_ship_destroyed: bool
    credits_transferred: int

class TeamManagementResponse(BaseModel):
    id: str
    name: str
    leader_id: str
    leader_name: str
    member_count: int
    total_credits: int
    created_at: datetime
    is_active: bool

class SystemHealthResponse(BaseModel):
    database_status: str
    api_response_time: float
    active_players: int
    active_sessions: int
    memory_usage: float
    cpu_usage: float
    last_checked: datetime

class AnalyticsDashboardResponse(BaseModel):
    player_engagement: Dict[str, Any]
    economic_health: Dict[str, Any]
    combat_activity: Dict[str, Any]
    exploration_progress: Dict[str, Any]
    server_performance: Dict[str, Any]

class AdminActionRequest(BaseModel):
    action_type: str
    target_type: str
    target_id: str
    description: str
    parameters: Optional[Dict[str, Any]] = None

class GameEventRequest(BaseModel):
    event_type: str
    title: str
    description: str
    affected_regions: List[str]  # Regions affected by this event
    duration_hours: int
    effects: List[Dict[str, Any]]

# Player Management Endpoints

@router.get("/players/comprehensive", response_model=Dict[str, Any])
async def get_players_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    search: Optional[str] = None,
    filter_active: Optional[bool] = None,
    filter_team: Optional[str] = None,
    sort_by: str = Query("created_at", pattern="^(username|credits|turns|created_at|last_login)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    include_assets: bool = Query(False, description="Include detailed asset information"),
    include_activity: bool = Query(False, description="Include activity metrics"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive player list with filtering, sorting, and pagination"""
    try:
        # Build base query with proper joins
        query = db.query(Player).join(User, Player.user_id == User.id)
        
        # Apply filters
        if search:
            query = query.filter(
                or_(
                    User.username.ilike(f"%{search}%"),
                    User.email.ilike(f"%{search}%")
                )
            )
        
        if filter_active is not None:
            query = query.filter(User.is_active == filter_active)
            
        if filter_team:
            query = query.filter(Player.team_id == filter_team)
        
        # Apply sorting
        if sort_by == "username":
            sort_column = User.username
        elif sort_by == "created_at":
            sort_column = User.created_at
        elif sort_by == "last_login":
            sort_column = Player.last_game_login
        else:
            sort_column = getattr(Player, sort_by)
            
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # Get total count before pagination
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        players = query.offset(offset).limit(limit).all()
        
        # Pre-calculate asset counts for all players in one query (more efficient)
        player_ids = [player.id for player in players]
        
        # Get asset counts with single queries
        ships_counts = {}
        planets_counts = {}
        ports_counts = {}
        
        if player_ids:
            # Count ships for all players
            ship_results = db.query(
                Ship.owner_id, 
                func.count(Ship.id).label('count')
            ).filter(Ship.owner_id.in_(player_ids)).group_by(Ship.owner_id).all()
            ships_counts = {str(result[0]): result[1] for result in ship_results}
            
            # Count planets for all players
            planet_results = db.query(
                Planet.owner_id,
                func.count(Planet.id).label('count')
            ).filter(Planet.owner_id.in_(player_ids)).group_by(Planet.owner_id).all()
            planets_counts = {str(result[0]): result[1] for result in planet_results}
            
            # Count ports for all players
            port_results = db.query(
                Station.owner_id,
                func.count(Station.id).label('count')
            ).filter(Station.owner_id.in_(player_ids)).group_by(Station.owner_id).all()
            ports_counts = {str(result[0]): result[1] for result in port_results}
        
        # Build response data
        players_data = []
        for player in players:
            player_id_str = str(player.id)
            
            # Get asset counts from pre-calculated data
            ships_count = ships_counts.get(player_id_str, 0)
            planets_count = planets_counts.get(player_id_str, 0)
            ports_count = ports_counts.get(player_id_str, 0)

            # Calculate total asset value
            total_asset_value = player.credits  # Start with credits

            # Add ship values (simplified calculation)
            if ships_count > 0:
                # Estimate ship values without complex queries
                estimated_ship_value = ships_count * 50000  # Default ship value
                total_asset_value += estimated_ship_value

            # Derive status from is_active (matching basic endpoint behavior)
            status = "active" if player.user.is_active else "inactive"

            # Build complete player data with all required fields
            player_data = {
                "id": player_id_str,
                "username": player.user.username,
                "email": player.user.email,
                "credits": player.credits,
                "turns": player.turns,
                "current_sector_id": player.current_sector_id,
                "current_ship_id": str(player.current_ship_id) if player.current_ship_id else None,
                "team_id": str(player.team_id) if player.team_id else None,
                "is_active": player.user.is_active,
                "status": status,  # Always include status field
                "last_login": player.last_game_login,
                "created_at": player.user.created_at,
                "ships_count": ships_count,
                "planets_count": planets_count,
                "ports_count": ports_count,
                # Always include assets object (not conditional)
                "assets": {
                    "ships_count": ships_count,
                    "planets_count": planets_count,
                    "ports_count": ports_count,
                    "total_value": total_asset_value
                },
                # Always include activity object (not conditional)
                "activity": {
                    "last_login": player.last_game_login.isoformat() if player.last_game_login else (player.user.created_at.isoformat() if player.user.created_at else None),
                    "session_count_today": 0,  # Would need session tracking
                    "actions_today": 0,  # Would need activity tracking
                    "total_trade_volume": 0,  # Would need trade history
                    "combat_rating": 0,  # Would need combat stats
                    "suspicious_activity": False  # Would need security analysis
                },
                # Always include ARIA field (null until data collection implemented)
                "aria": None
            }
            
            players_data.append(player_data)
        
        return {
            "players": players_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_players_comprehensive: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch players: {str(e)}")

@router.put("/players/{player_id}", response_model=Dict[str, str])
async def update_player(
    player_id: str,
    update_data: PlayerUpdateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update player data"""
    try:
        player = db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Update basic fields
        if update_data.credits is not None:
            player.credits = update_data.credits
        if update_data.turns is not None:
            player.turns = update_data.turns
        if update_data.is_active is not None:
            player.user.is_active = update_data.is_active
        
        # Handle reputation adjustments
        if update_data.reputation_adjustments:
            for faction, adjustment in update_data.reputation_adjustments.items():
                if faction in player.reputation:
                    current_rep = player.reputation[faction].get('value', 0)
                    new_rep = max(-800, min(800, current_rep + adjustment))
                    player.reputation[faction]['value'] = new_rep
                    # Update level based on new value
                    player.reputation[faction]['level'] = calculate_reputation_level(new_rep)
        
        db.commit()
        
        # Log admin action
        logger.info(f"Admin {current_admin.username} updated player {player.user.username}")
        
        return {"message": "Player updated successfully"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating player {player_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update player: {str(e)}")

# Ship Management Endpoints

@router.get("/ships/comprehensive", response_model=Dict[str, Any])
async def get_ships_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_type: Optional[str] = None,
    filter_sector: Optional[int] = None,
    filter_owner: Optional[str] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive ship list with filtering"""
    try:
        # Build query with proper error handling
        query = db.query(Ship)
        
        # Apply filters
        if filter_type:
            try:
                from src.models.ship import ShipType
                ship_type_enum = ShipType(filter_type)
                query = query.filter(Ship.type == ship_type_enum)
            except ValueError:
                # If invalid ship type, return empty result
                return {
                    "ships": [],
                    "total_count": 0,
                    "page": page,
                    "limit": limit,
                    "total_pages": 0
                }
        
        if filter_sector:
            query = query.filter(Ship.sector_id == filter_sector)
        
        if filter_owner:
            # Join with Player and User for owner filtering
            query = query.join(Player, Ship.owner_id == Player.id).join(User, Player.user_id == User.id)
            query = query.filter(User.username.ilike(f"%{filter_owner}%"))
        
        # Get total count - handle any errors gracefully
        try:
            total_count = query.count()
        except Exception as e:
            logger.error(f"Error counting ships: {e}")
            total_count = 0
        
        # Apply pagination
        offset = (page - 1) * limit
        try:
            ships = query.offset(offset).limit(limit).all()
        except Exception as e:
            logger.error(f"Error fetching ships: {e}")
            ships = []
        
        # Build response data with safe field access
        ships_data = []
        for ship in ships:
            try:
                # Get owner information safely
                owner_name = "Unknown"
                try:
                    if hasattr(ship, 'owner') and ship.owner:
                        if hasattr(ship.owner, 'user') and ship.owner.user:
                            owner_name = ship.owner.user.username
                        else:
                            # Try to fetch the user separately
                            owner = db.query(Player).join(User).filter(Player.id == ship.owner_id).first()
                            if owner and owner.user:
                                owner_name = owner.user.username
                except Exception as e:
                    logger.warning(f"Failed to resolve ship owner name for ship {ship.id}: {e}")

                # Extract maintenance rating from maintenance JSONB field
                maintenance_data = getattr(ship, 'maintenance', {}) or {}
                maintenance_rating = maintenance_data.get('current_rating', 100.0)
                
                # Extract cargo info from cargo JSONB field  
                cargo_data = getattr(ship, 'cargo', {}) or {}
                cargo_used = cargo_data.get('used_capacity', 0)
                cargo_capacity = cargo_data.get('max_capacity', 1000)  # Default capacity
                
                ships_data.append({
                    "id": str(ship.id),
                    "name": getattr(ship, 'name', f"Ship {ship.id}"),
                    "ship_type": ship.type.value if hasattr(ship, 'type') and ship.type else "UNKNOWN",
                    # NPC ships have no owner — emit null, not the string "None"
                    "owner_id": str(ship.owner_id) if ship.owner_id else None,
                    "owner_name": owner_name,
                    "is_npc": bool(getattr(ship, 'is_npc', False)),
                    "current_sector_id": getattr(ship, 'sector_id', 1),
                    "maintenance_rating": maintenance_rating,
                    "cargo_used": cargo_used,
                    "cargo_capacity": cargo_capacity,
                    "is_active": getattr(ship, 'is_active', True),
                    "created_at": ship.created_at if hasattr(ship, 'created_at') else None
                })
            except Exception as e:
                logger.error(f"Error processing ship {ship.id}: {e}")
                # Add minimal ship info even if detailed processing fails
                ships_data.append({
                    "id": str(ship.id),
                    "name": f"Ship {ship.id}",
                    "ship_type": "UNKNOWN",
                    "owner_id": str(ship.owner_id) if getattr(ship, 'owner_id', None) else None,
                    "owner_name": "Unknown",
                    "is_npc": bool(getattr(ship, 'is_npc', False)),
                    "current_sector_id": 1,
                    "maintenance_rating": 100.0,
                    "cargo_used": 0,
                    "cargo_capacity": 1000,
                    "is_active": True,
                    "created_at": None
                })
        
        return {
            "ships": ships_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if total_count > 0 else 0
        }
        
    except Exception as e:
        logger.error(f"Error in get_ships_comprehensive: {e}")
        # Return empty result instead of failing
        return {
            "ships": [],
            "total_count": 0,
            "page": page,
            "limit": limit,
            "total_pages": 0
        }

class ShipCreateRequest(BaseModel):
    name: str
    ship_type: str
    owner_id: str
    current_sector_id: int
    
class ShipUpdateRequest(BaseModel):
    name: Optional[str] = None
    owner_id: Optional[str] = None
    current_sector_id: Optional[int] = None
    is_active: Optional[bool] = None

@router.post("/ships", response_model=Dict[str, str])
async def create_ship(
    ship_data: ShipCreateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new ship for a player"""
    try:
        # Verify player exists
        player = db.query(Player).filter(Player.id == ship_data.owner_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        
        # Verify sector exists
        sector = db.query(Sector).filter(Sector.sector_id == ship_data.current_sector_id).first()
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Create new ship
        from src.models.ship import ShipType
        new_ship = Ship(
            id=uuid.uuid4(),
            name=ship_data.name,
            type=ShipType(ship_data.ship_type),
            owner_id=ship_data.owner_id,
            sector_id=ship_data.current_sector_id,
            maintenance={"current_rating": 100.0},
            cargo={"used_capacity": 0, "max_capacity": 1000},
            combat={"shields": 100, "max_shields": 100, "hull": 100, "max_hull": 100},
            base_speed=10.0,
            current_speed=10.0,
            turn_cost=1,
            purchase_value=50000,
            current_value=50000,
            is_active=True
        )
        
        db.add(new_ship)
        db.commit()
        
        logger.info(f"Admin {current_admin.username} created ship {ship_data.name} for player {player.user.username}")
        
        return {"message": "Ship created successfully", "ship_id": str(new_ship.id)}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating ship: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create ship: {str(e)}")

@router.put("/ships/{ship_id}", response_model=Dict[str, str])
async def update_ship(
    ship_id: str,
    ship_data: ShipUpdateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update ship properties"""
    try:
        ship = db.query(Ship).filter(Ship.id == ship_id).first()
        if not ship:
            raise HTTPException(status_code=404, detail="Ship not found")
        
        # Update fields if provided
        if ship_data.name is not None:
            ship.name = ship_data.name
        if ship_data.owner_id is not None:
            # Verify new owner exists
            player = db.query(Player).filter(Player.id == ship_data.owner_id).first()
            if not player:
                raise HTTPException(status_code=404, detail="New owner not found")
            ship.owner_id = ship_data.owner_id
        if ship_data.current_sector_id is not None:
            # Verify sector exists
            sector = db.query(Sector).filter(Sector.sector_id == ship_data.current_sector_id).first()
            if not sector:
                raise HTTPException(status_code=404, detail="Sector not found")
            ship.sector_id = ship_data.current_sector_id
        if ship_data.is_active is not None:
            ship.is_active = ship_data.is_active
        
        db.commit()
        
        logger.info(f"Admin {current_admin.username} updated ship {ship.name}")
        
        return {"message": "Ship updated successfully"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating ship {ship_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update ship: {str(e)}")

@router.delete("/ships/{ship_id}", response_model=Dict[str, str])
async def delete_ship(
    ship_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a ship"""
    try:
        ship = db.query(Ship).filter(Ship.id == ship_id).first()
        if not ship:
            raise HTTPException(status_code=404, detail="Ship not found")
        
        ship_name = ship.name
        owner = db.query(Player).join(User).filter(Player.id == ship.owner_id).first()
        owner_name = owner.user.username if owner else "Unknown"
        
        # If this is the player's current ship, clear it
        if owner and owner.current_ship_id == ship.id:
            owner.current_ship_id = None
        
        db.delete(ship)
        db.commit()
        
        logger.info(f"Admin {current_admin.username} deleted ship {ship_name} from player {owner_name}")
        
        return {"message": "Ship deleted successfully"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting ship {ship_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete ship: {str(e)}")

@router.post("/ships/{ship_id}/teleport", response_model=Dict[str, str])
async def teleport_ship(
    ship_id: str,
    target_sector_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Teleport a ship to a different sector"""
    try:
        ship = db.query(Ship).filter(Ship.id == ship_id).first()
        if not ship:
            raise HTTPException(status_code=404, detail="Ship not found")
        
        # Verify target sector exists
        sector = db.query(Sector).filter(Sector.sector_id == target_sector_id).first()
        if not sector:
            raise HTTPException(status_code=404, detail="Target sector not found")
        
        old_sector = ship.sector_id
        ship.sector_id = target_sector_id
        
        # Also update player location if this is their current ship.
        # Keep current_region_id in sync with the target sector — teleports
        # can cross regions, and region-filtered routes (e.g.
        # /player/current-sector) 404 on a stale region.
        owner = db.query(Player).filter(Player.id == ship.owner_id).first()
        if owner and owner.current_ship_id == ship.id:
            owner.current_sector_id = target_sector_id
            owner.current_region_id = sector.region_id
        
        db.commit()
        
        logger.info(f"Admin {current_admin.username} teleported ship {ship.name} from sector {old_sector} to {target_sector_id}")
        
        return {"message": f"Ship teleported to sector {target_sector_id}"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error teleporting ship {ship_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to teleport ship: {str(e)}")

# Player Management Endpoints

@router.post("/players/create-from-user", response_model=Dict[str, str])
async def create_player_from_user(
    user_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a player account from an existing user account"""
    try:
        # Check if user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if player already exists for this user
        existing_player = db.query(Player).filter(Player.user_id == user.id).first()
        if existing_player:
            raise HTTPException(status_code=400, detail="Player already exists for this user")
        
        # Create new player
        new_player = Player(
            user_id=user.id,
            credits=10000,  # Starting credits
            turns=1000,     # Starting turns
            current_sector_id=1  # Default starting sector
        )
        
        db.add(new_player)
        db.commit()
        db.refresh(new_player)
        
        logger.info(f"Admin {current_admin.username} created player for user {user.username}")
        
        return {"message": f"Player created successfully for user {user.username}", "player_id": str(new_player.id)}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating player for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create player: {str(e)}")

@router.post("/players/create-bulk", response_model=Dict[str, Any])
async def create_players_from_all_users(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create player accounts for all users who don't have them"""
    try:
        # Get all users who don't have player accounts
        users_without_players = db.query(User).filter(
            ~User.id.in_(db.query(Player.user_id))
        ).all()
        
        created_count = 0
        for user in users_without_players:
            new_player = Player(
                user_id=user.id,
                credits=10000,  # Starting credits
                turns=1000,     # Starting turns
                current_sector_id=1  # Default starting sector
            )
            db.add(new_player)
            created_count += 1
        
        db.commit()
        
        logger.info(f"Admin {current_admin.username} created {created_count} players from existing users")
        
        return {
            "message": f"Successfully created {created_count} players", 
            "created_count": created_count,
            "total_users": len(users_without_players)
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating players from users: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create players: {str(e)}")

# Universe Management Endpoints

@router.get("/universe/sectors/comprehensive", response_model=Dict[str, Any])
async def get_sectors_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    filter_type: Optional[str] = None,
    filter_region: Optional[str] = None,
    filter_zone: Optional[str] = None,
    filter_discovered: Optional[bool] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive sector information"""
    try:
        query = db.query(Sector).join(Cluster).join(Region)
        
        # Apply filters
        if filter_type:
            query = query.filter(Sector.type == filter_type)
        if filter_region:
            query = query.filter(Region.id == filter_region)
        if filter_zone:
            query = query.filter(Sector.zone_id == filter_zone)
        if filter_discovered is not None:
            query = query.filter(Sector.is_discovered == filter_discovered)
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        sectors = query.offset(offset).limit(limit).all()
        
        # Build response data
        sectors_data = []
        for sector in sectors:
            # Check for ports and planets
            has_port = db.query(Station).filter(Station.sector_id == sector.sector_id).first() is not None
            has_planet = db.query(Planet).filter(Planet.sector_id == sector.sector_id).first() is not None
            has_warp_tunnel = db.query(WarpTunnel).filter(
                or_(WarpTunnel.origin_sector_id == sector.id, WarpTunnel.destination_sector_id == sector.id)
            ).first() is not None
            
            # Count players in sector
            player_count = db.query(Player).filter(Player.current_sector_id == sector.sector_id).count()
            
            sectors_data.append(SectorManagementResponse(
                id=str(sector.id),
                sector_id=sector.sector_id,
                name=sector.name,
                type=sector.type.value,
                cluster_id=str(sector.cluster_id),
                region_name=sector.region.display_name if sector.region else "Unknown",
                zone_id=str(sector.zone_id) if sector.zone_id else None,
                zone_name=sector.zone.name if sector.zone else None,
                zone_type=sector.zone.zone_type if sector.zone else None,
                x_coord=sector.x_coord,
                y_coord=sector.y_coord,
                z_coord=sector.z_coord,
                hazard_level=sector.hazard_level,
                is_discovered=sector.is_discovered,
                has_port=has_port,
                has_planet=has_planet,
                has_warp_tunnel=has_warp_tunnel,
                player_count=player_count,
                controlling_faction=sector.controlling_faction
            ))
        
        return {
            "sectors": sectors_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_sectors_comprehensive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch sectors: {str(e)}")

# Station Management Endpoints

@router.get("/ports/comprehensive", response_model=Dict[str, Any])
async def get_ports_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_class: Optional[str] = None,
    filter_type: Optional[str] = None,
    filter_owner: Optional[str] = None,
    owner_id: Optional[str] = Query(None, description="Filter by exact player/owner UUID"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive port information"""
    try:
        query = db.query(Station)

        # Apply filters
        if filter_class:
            query = query.filter(Station.station_class == filter_class)
        if filter_type:
            query = query.filter(Station.type == filter_type)
        if owner_id:
            # Filter by exact owner ID (UUID)
            try:
                from uuid import UUID
                owner_uuid = UUID(owner_id)
                query = query.filter(Station.owner_id == owner_uuid)
            except ValueError:
                # Invalid UUID, return empty result
                return {
                    "ports": [],
                    "total_count": 0,
                    "page": page,
                    "limit": limit,
                    "total_pages": 0
                }
        elif filter_owner:
            # Filter by username pattern (legacy support)
            query = query.join(Player).join(User).filter(User.username.ilike(f"%{filter_owner}%"))
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        ports = query.offset(offset).limit(limit).all()
        
        # Build response data
        ports_data = []
        for port in ports:
            owner_name = None
            if port.owner_id:
                owner = db.query(Player).join(User).filter(Player.id == port.owner_id).first()
                if owner:
                    owner_name = owner.user.username

            # Get sector name
            sector_name = None
            if port.sector:
                sector_name = port.sector.name

            # Extract information from JSONB fields with defaults
            defenses = port.defenses or {}
            service_prices = port.service_prices or {}
            commodities_data = port.commodities or {}

            # Calculate values for frontend display
            trade_volume = port.trade_volume or 0
            # Calculate max capacity from commodities
            max_capacity = sum(commodity.get("capacity", 1000) for commodity in commodities_data.values()) if commodities_data else 10000
            security_level = defenses.get("defense_drones", 0) + defenses.get("patrol_ships", 0)
            docking_fee = service_prices.get("docking_fee", 100)

            # Extract commodities from commodities data
            commodities = list(commodities_data.keys()) if commodities_data else []

            ports_data.append(StationManagementResponse(
                id=str(port.id),
                name=port.name,
                sector_id=str(port.sector_id),
                sector_name=sector_name,
                port_class=port.station_class.name,
                trade_volume=trade_volume,
                max_capacity=max_capacity,
                security_level=security_level,
                docking_fee=docking_fee,
                owner_id=str(port.owner_id) if port.owner_id else None,
                owner_name=owner_name,
                created_at=port.created_at.isoformat() if port.created_at else "",
                is_operational=port.status == StationStatus.OPERATIONAL,
                commodities=commodities
            ))
        
        return {
            "ports": ports_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_ports_comprehensive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch ports: {str(e)}")

# Planet Management Endpoints

@router.get("/planets/comprehensive", response_model=Dict[str, Any])
async def get_planets_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_type: Optional[str] = None,
    filter_owner: Optional[str] = None,
    filter_colonized: Optional[bool] = None,
    owner_id: Optional[str] = Query(None, description="Filter by exact player/owner UUID"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive planet information"""
    try:
        query = db.query(Planet)

        # Apply filters
        if filter_type:
            query = query.filter(Planet.type == filter_type)
        if owner_id:
            # Filter by exact owner ID (UUID)
            try:
                from uuid import UUID
                owner_uuid = UUID(owner_id)
                query = query.filter(Planet.owner_id == owner_uuid)
            except ValueError:
                # Invalid UUID, return empty result
                return {
                    "planets": [],
                    "total_count": 0,
                    "page": page,
                    "limit": limit,
                    "total_pages": 0
                }
        elif filter_owner:
            # Filter by username pattern (legacy support)
            query = query.join(Player).join(User).filter(User.username.ilike(f"%{filter_owner}%"))
        if filter_colonized is not None:
            if filter_colonized:
                query = query.filter(Planet.owner_id.isnot(None))
            else:
                query = query.filter(Planet.owner_id.is_(None))
        
        # Get total count
        total_count = query.count()

        # Apply pagination with explicit ordering by sector ID
        offset = (page - 1) * limit
        planets = query.order_by(Planet.sector_id.asc()).offset(offset).limit(limit).all()
        
        # Build response data
        planets_data = []
        for planet in planets:
            owner_name = None
            if planet.owner_id:
                owner = db.query(Player).join(User).filter(Player.id == planet.owner_id).first()
                if owner:
                    owner_name = owner.user.username
            
            planets_data.append(PlanetManagementResponse(
                id=str(planet.id),
                name=planet.name,
                sector_id=planet.sector_id,
                planet_type=planet.type.value,
                owner_id=str(planet.owner_id) if planet.owner_id else None,
                owner_name=owner_name,
                population=planet.population,
                max_population=planet.max_population,
                habitability_score=planet.habitability_score,
                resource_richness=planet.resource_richness,
                defense_level=planet.defense_level,
                colonized_at=planet.colonized_at,
                genesis_created=planet.genesis_created
            ))
        
        return {
            "planets": planets_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_planets_comprehensive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch planets: {str(e)}")

# Analytics and Monitoring Endpoints

@router.get("/analytics/dashboard", response_model=AnalyticsDashboardResponse)
async def get_analytics_dashboard(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive analytics dashboard"""
    try:
        now = datetime.utcnow()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        
        # Player Engagement Metrics
        total_players = db.query(Player).count()
        active_24h = db.query(Player).filter(Player.last_game_login >= day_ago).count()
        active_7d = db.query(Player).filter(Player.last_game_login >= week_ago).count()
        active_30d = db.query(Player).filter(Player.last_game_login >= month_ago).count()
        new_registrations = db.query(User).filter(User.created_at >= day_ago).count()
        
        # Economic Health Metrics
        total_credits = db.query(func.sum(Player.credits)).scalar() or 0
        avg_credits = db.query(func.avg(Player.credits)).scalar() or 0
        
        # Combat Activity Metrics
        combat_24h = db.query(func.count()).select_from(text("combat_logs")).filter(
            text("timestamp >= :day_ago")
        ).params(day_ago=day_ago).scalar() or 0
        
        # Exploration Progress
        total_sectors = db.query(Sector).count()
        discovered_sectors = db.query(Sector).filter(Sector.is_discovered == True).count()
        exploration_percentage = (discovered_sectors / total_sectors * 100) if total_sectors > 0 else 0
        
        return AnalyticsDashboardResponse(
            player_engagement={
                "daily_active_users": active_24h,
                "weekly_active_users": active_7d,
                "monthly_active_users": active_30d,
                "new_registrations_24h": new_registrations,
                "total_players": total_players
            },
            economic_health={
                "total_credits_in_circulation": total_credits,
                "average_player_wealth": avg_credits,
                "active_traders_24h": 0  # Would need trade transaction table
            },
            combat_activity={
                "combat_events_24h": combat_24h,
                "active_sectors": discovered_sectors
            },
            exploration_progress={
                "total_sectors": total_sectors,
                "discovered_sectors": discovered_sectors,
                "exploration_percentage": exploration_percentage
            },
            server_performance={
                "active_players": active_24h,
                "response_time": 0.1,  # Would need actual monitoring
                "memory_usage": 0,     # Would need actual monitoring
                "cpu_usage": 0         # Would need actual monitoring
            }
        )
        
    except Exception as e:
        logger.error(f"Error in get_analytics_dashboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")

@router.get("/system/health", response_model=SystemHealthResponse)
async def get_system_health(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get system health status"""
    try:
        # Test database connectivity
        start_time = datetime.utcnow()
        db.execute(text("SELECT 1"))
        db_response_time = (datetime.utcnow() - start_time).total_seconds()
        
        # Get active player count
        day_ago = datetime.utcnow() - timedelta(days=1)
        active_players = db.query(Player).filter(Player.last_game_login >= day_ago).count()
        
        return SystemHealthResponse(
            database_status="HEALTHY" if db_response_time < 1.0 else "WARNING",
            api_response_time=db_response_time,
            active_players=active_players,
            active_sessions=active_players,  # Simplified
            memory_usage=0.0,  # Would need actual monitoring
            cpu_usage=0.0,     # Would need actual monitoring
            last_checked=datetime.utcnow()
        )
        
    except Exception as e:
        logger.error(f"Error in get_system_health: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get system health: {str(e)}")

# Warp Tunnel Management Endpoints

class WarpTunnelManagementResponse(BaseModel):
    id: str
    name: str
    origin_sector_id: int
    destination_sector_id: int
    type: str
    status: str
    stability: float
    is_bidirectional: bool
    turn_cost: int
    energy_cost: int
    travel_time: int  # Same as turn_cost for frontend compatibility
    max_ship_size: str
    is_active: bool
    total_traversals: int
    created_at: datetime

@router.get("/warp-tunnels/comprehensive", response_model=Dict[str, Any])
async def get_warp_tunnels_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_type: Optional[str] = None,
    filter_status: Optional[str] = None,
    filter_origin_sector: Optional[int] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive warp tunnel information"""
    try:
        query = db.query(WarpTunnel)
        
        # Apply filters
        if filter_type:
            query = query.filter(WarpTunnel.type == filter_type)
        if filter_status:
            query = query.filter(WarpTunnel.status == filter_status)
        if filter_origin_sector:
            # Need to join with sectors to filter by sector_id (integer)
            query = query.join(Sector, WarpTunnel.origin_sector_id == Sector.id).filter(Sector.sector_id == filter_origin_sector)
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        warp_tunnels = query.offset(offset).limit(limit).all()
        
        # Build response data
        tunnels_data = []
        for tunnel in warp_tunnels:
            # Get origin and destination sector integers for display
            origin_sector_int = None
            dest_sector_int = None
            
            if tunnel.origin_sector:
                origin_sector_int = tunnel.origin_sector.sector_id
            if tunnel.destination_sector:
                dest_sector_int = tunnel.destination_sector.sector_id
            
            # Extract active status from tunnel_status JSONB
            tunnel_status_data = tunnel.tunnel_status or {}
            is_active = tunnel_status_data.get("is_active", True) and tunnel.status.value == "ACTIVE"
            
            # Determine max ship size from access requirements or default
            access_reqs = tunnel.access_requirements or {}
            max_ship_size = access_reqs.get("max_ship_size", "LARGE")
            
            tunnels_data.append(WarpTunnelManagementResponse(
                id=str(tunnel.id),
                name=tunnel.name,
                origin_sector_id=origin_sector_int or 0,
                destination_sector_id=dest_sector_int or 0,
                type=tunnel.type.value,
                status=tunnel.status.value,
                stability=tunnel.stability,
                is_bidirectional=tunnel.is_bidirectional,
                turn_cost=tunnel.turn_cost,
                energy_cost=tunnel.energy_cost,
                travel_time=tunnel.turn_cost,  # Frontend expects travel_time, same as turn_cost
                max_ship_size=max_ship_size,
                is_active=is_active,
                total_traversals=tunnel.total_traversals,
                created_at=tunnel.created_at
            ))
        
        return {
            "warp_tunnels": tunnels_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_warp_tunnels_comprehensive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch warp tunnels: {str(e)}")

# Team Management Endpoints

@router.get("/teams/comprehensive", response_model=Dict[str, Any])
async def get_teams_comprehensive(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get comprehensive team information"""
    try:
        query = db.query(Team)
        
        # Get total count
        total_count = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        teams = query.offset(offset).limit(limit).all()
        
        # Build response data
        teams_data = []
        for team in teams:
            # Get team statistics
            members = db.query(Player).filter(Player.team_id == team.id).all()
            member_count = len(members)
            total_credits = sum(member.credits for member in members)
            
            # Get leader info
            leader = db.query(Player).join(User).filter(Player.id == team.leader_id).first()
            leader_name = leader.user.username if leader else "Unknown"
            
            teams_data.append(TeamManagementResponse(
                id=str(team.id),
                name=team.name,
                leader_id=str(team.leader_id),
                leader_name=leader_name,
                member_count=member_count,
                total_credits=total_credits,
                created_at=team.created_at,
                is_active=team.is_active
            ))
        
        return {
            "teams": teams_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }
        
    except Exception as e:
        logger.error(f"Error in get_teams_comprehensive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch teams: {str(e)}")

# Helper Functions

def calculate_reputation_level(value: int) -> str:
    """Calculate reputation level from value"""
    if value >= 701:
        return "Exalted"
    elif value >= 601:
        return "Revered"
    elif value >= 501:
        return "Honored"
    elif value >= 401:
        return "Valued"
    elif value >= 301:
        return "Respected"
    elif value >= 201:
        return "Trusted"
    elif value >= 101:
        return "Acknowledged"
    elif value >= 1:
        return "Recognized"
    elif value == 0:
        return "Neutral"
    elif value >= -100:
        return "Questionable"
    elif value >= -200:
        return "Suspicious"
    elif value >= -300:
        return "Untrustworthy"
    elif value >= -400:
        return "Smuggler"
    elif value >= -500:
        return "Pirate"
    elif value >= -600:
        return "Outlaw"
    elif value >= -700:
        return "Criminal"
    else:
        return "Public Enemy"


# Analytics Endpoints

@router.get("/analytics/real-time", response_model=Dict[str, Any])
async def get_real_time_analytics(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get real-time analytics data from the database
    """
    try:
        analytics_service = AnalyticsService(db)
        metrics = analytics_service.get_real_time_metrics()
        
        logger.info(f"Admin {current_admin.username} requested real-time analytics")
        
        return {
            "success": True,
            "data": metrics,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error fetching real-time analytics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {str(e)}")


@router.post("/analytics/snapshot", response_model=Dict[str, Any])
async def create_analytics_snapshot(
    snapshot_type: str = "manual",
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Create an analytics snapshot for historical tracking
    """
    try:
        analytics_service = AnalyticsService(db)
        snapshot = analytics_service.create_analytics_snapshot(snapshot_type)
        
        logger.info(f"Admin {current_admin.username} created analytics snapshot: {snapshot_type}")
        
        return {
            "success": True,
            "message": f"Analytics snapshot created successfully",
            "snapshot_id": str(snapshot.id),
            "timestamp": snapshot.snapshot_time.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error creating analytics snapshot: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create snapshot: {str(e)}")


@router.post("/ports/update-stock-levels", response_model=Dict[str, Any])
async def update_all_port_stock_levels(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Update stock levels for all existing ports to match their trading roles.
    This ensures ports have appropriate inventory for the commodities they trade.
    """
    try:
        from src.models.station import Station
        
        # Get all ports
        ports = db.query(Station).all()
        
        updated_ports = []
        
        for port in ports:
            # Store original stock levels for reporting
            original_commodities = dict(port.commodities)

            # Update trading flags and stock levels
            port.update_commodity_trading_flags()
            port.update_commodity_stock_levels()

            # Track changes
            changes = {}
            for commodity_name, commodity_data in port.commodities.items():
                old_quantity = original_commodities.get(commodity_name, {}).get("quantity", 0)
                new_quantity = commodity_data.get("quantity", 0)
                if old_quantity != new_quantity:
                    changes[commodity_name] = {
                        "old_quantity": old_quantity,
                        "new_quantity": new_quantity
                    }

            if changes:
                updated_ports.append({
                    "station_id": str(port.id),
                    "station_name": port.name,
                    "station_class": port.station_class.value,
                    "station_type": port.type.value,
                    "sector_id": port.sector_id,
                    "changes": changes
                })
        
        # Commit all changes
        db.commit()
        
        logger.info(f"Admin {current_admin.username} updated stock levels for {len(updated_ports)} ports")
        
        return {
            "success": True,
            "message": f"Updated stock levels for {len(updated_ports)} ports out of {len(ports)} total",
            "ports_updated": len(updated_ports),
            "total_ports": len(ports),
            "updated_ports": updated_ports[:20],  # Limit response size, show first 20
            "has_more": len(updated_ports) > 20
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating port stock levels: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update port stock levels: {str(e)}")


# =============================================================================
# AI SECURITY MONITORING ENDPOINTS
# =============================================================================

@router.get("/security/report", summary="Get comprehensive security report")
async def get_security_report(current_admin: User = Depends(get_current_admin)):
    """
    Get comprehensive security monitoring report including:
    - Player statistics (blocked, high risk, etc.)
    - Violation statistics by type
    - API cost usage and limits
    - Current rate limits
    """
    try:
        security_service = get_security_service()
        report = security_service.generate_security_report()
        
        logger.info(f"Admin {current_admin.username} generated security report")
        return report
        
    except Exception as e:
        logger.error(f"Error generating security report: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate security report: {str(e)}")


@router.get("/security/alerts", summary="Get current security alerts")
async def get_security_alerts(current_admin: User = Depends(get_current_admin)):
    """
    Get current security alerts that need admin attention:
    - High cost usage warnings
    - Multiple violations by players
    - Currently blocked players
    """
    try:
        security_service = get_security_service()
        alerts = security_service.get_security_alerts()
        
        logger.info(f"Admin {current_admin.username} checked security alerts")
        return {
            "alerts": alerts,
            "alert_count": len(alerts),
            "high_priority_count": sum(1 for alert in alerts if alert.get("severity") == "high")
        }
        
    except Exception as e:
        logger.error(f"Error getting security alerts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get security alerts: {str(e)}")


@router.get("/security/player/{player_id}/risk", summary="Get player risk assessment")
async def get_player_risk_assessment(
    player_id: str,
    current_admin: User = Depends(get_current_admin)
):
    """
    Get detailed risk assessment for a specific player including:
    - Risk level and score
    - Risk factors (trust score, violations, etc.)
    - Current blocking status
    - API cost usage
    """
    try:
        security_service = get_security_service()
        assessment = security_service.get_player_risk_assessment(player_id)
        
        logger.info(f"Admin {current_admin.username} assessed risk for player {player_id}")
        return assessment
        
    except Exception as e:
        logger.error(f"Error getting player risk assessment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get player risk assessment: {str(e)}")


@router.get("/security/player/{player_id}/status", summary="Get player security status")
async def get_player_security_status(
    player_id: str,
    current_admin: User = Depends(get_current_admin)
):
    """
    Get current security status for a specific player including:
    - Block status and expiration
    - Trust score and violation count
    - Request counts and rate limiting
    """
    try:
        security_service = get_security_service()
        status = security_service.get_player_security_status(player_id)
        
        logger.info(f"Admin {current_admin.username} checked status for player {player_id}")
        return status
        
    except Exception as e:
        logger.error(f"Error getting player security status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get player security status: {str(e)}")


@router.post("/security/cleanup", summary="Clean up old security data")
async def cleanup_security_data(
    days_to_keep: int = Query(default=7, ge=1, le=30, description="Number of days of data to keep"),
    current_admin: User = Depends(get_current_admin)
):
    """
    Clean up old security tracking data to prevent memory growth.
    Removes cost tracking and violation data older than specified days.
    """
    try:
        security_service = get_security_service()
        security_service.cleanup_old_data(days_to_keep)
        
        logger.info(f"Admin {current_admin.username} cleaned up security data (keeping {days_to_keep} days)")
        return {
            "success": True,
            "message": f"Cleaned up security data older than {days_to_keep} days",
            "days_kept": days_to_keep
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up security data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clean up security data: {str(e)}")


class PlayerSecurityAction(BaseModel):
    action: str = Field(..., description="Action to take: 'block', 'unblock', 'reset_violations', 'reset_trust'")
    duration_hours: Optional[int] = Field(None, description="Block duration in hours (for 'block' action)")
    reason: Optional[str] = Field(None, description="Reason for the action")


@router.post("/security/player/{player_id}/action", summary="Take security action on player")
async def take_security_action(
    player_id: str,
    action: PlayerSecurityAction,
    current_admin: User = Depends(get_current_admin)
):
    """
    Take security action on a player:
    - block: Block player for specified duration
    - unblock: Immediately unblock player
    - reset_violations: Reset violation count to 0
    - reset_trust: Reset trust score to 1.0
    """
    try:
        security_service = get_security_service()
        profile = security_service.get_or_create_player_profile(player_id)
        
        if action.action == "block":
            if action.duration_hours is None:
                raise HTTPException(status_code=400, detail="duration_hours required for block action")
            
            profile.is_blocked = True
            profile.block_expires = datetime.utcnow() + timedelta(hours=action.duration_hours)
            message = f"Player {player_id} blocked for {action.duration_hours} hours"
            
        elif action.action == "unblock":
            profile.is_blocked = False
            profile.block_expires = None
            message = f"Player {player_id} unblocked"
            
        elif action.action == "reset_violations":
            profile.violation_count = 0
            profile.last_violation = None
            message = f"Violation count reset for player {player_id}"
            
        elif action.action == "reset_trust":
            profile.trust_score = 1.0
            message = f"Trust score reset for player {player_id}"
            
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")
        
        logger.info(f"Admin {current_admin.username} took security action '{action.action}' on player {player_id}: {action.reason}")
        
        return {
            "success": True,
            "message": message,
            "action": action.action,
            "player_id": player_id,
            "reason": action.reason,
            "new_status": security_service.get_player_security_status(player_id)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error taking security action: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to take security action: {str(e)}")


# =============================================================================
# SIMPLE REDIRECT ENDPOINTS FOR FRONTEND COMPATIBILITY
# =============================================================================

@router.get("/planets", response_model=Dict[str, Any])
async def get_planets(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_type: Optional[str] = None,
    filter_owner: Optional[str] = None,
    filter_colonized: Optional[bool] = None,
    owner_id: Optional[str] = Query(None, description="Filter by exact player/owner UUID"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Simple planets endpoint that redirects to comprehensive endpoint"""
    return await get_planets_comprehensive(
        page=page,
        limit=limit,
        filter_type=filter_type,
        filter_owner=filter_owner,
        filter_colonized=filter_colonized,
        owner_id=owner_id,
        current_admin=current_admin,
        db=db
    )

@router.get("/ports", response_model=Dict[str, Any])
async def get_ports(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_class: Optional[str] = None,
    filter_type: Optional[str] = None,
    filter_owner: Optional[str] = None,
    owner_id: Optional[str] = Query(None, description="Filter by exact player/owner UUID"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Simple ports endpoint that redirects to comprehensive endpoint"""
    return await get_ports_comprehensive(
        page=page,
        limit=limit,
        filter_class=filter_class,
        filter_type=filter_type,
        filter_owner=filter_owner,
        owner_id=owner_id,
        current_admin=current_admin,
        db=db
    )

@router.get("/sectors", response_model=Dict[str, Any])
async def get_sectors(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    filter_type: Optional[str] = None,
    filter_region: Optional[str] = None,
    filter_discovered: Optional[bool] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Simple sectors endpoint that redirects to comprehensive endpoint"""
    return await get_sectors_comprehensive(
        page=page,
        limit=limit,
        filter_type=filter_type,
        filter_region=filter_region,
        filter_discovered=filter_discovered,
        current_admin=current_admin,
        db=db
    )

@router.put("/sectors/{sector_id}", response_model=Dict[str, Any])
async def update_sector(
    sector_id: str,
    sector_data: SectorUpdateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a sector's properties"""
    try:
        # Convert sector_id to UUID if it looks like a UUID, otherwise treat as sector_id number
        if len(sector_id) > 10:  # UUID length check
            sector = db.query(Sector).filter(Sector.id == uuid.UUID(sector_id)).first()
        else:
            sector = db.query(Sector).filter(Sector.sector_id == int(sector_id)).first()
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Import SectorType enum for validation
        from src.models.sector import SectorType
        
        # Update basic fields
        update_data = sector_data.dict(exclude_unset=True)
        
        for field, value in update_data.items():
            if field == "type" and value:
                # Validate and convert sector type
                try:
                    sector_type = SectorType(value)
                    setattr(sector, field, sector_type)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid sector type: {value}")
            elif field == "discovered_by_id" and value:
                # Validate player exists
                player = db.query(Player).filter(Player.id == uuid.UUID(value)).first()
                if not player:
                    raise HTTPException(status_code=400, detail="Invalid discovered_by_id: player not found")
                setattr(sector, field, uuid.UUID(value))
            elif field == "controlling_team_id" and value:
                # Validate team exists
                team = db.query(Team).filter(Team.id == uuid.UUID(value)).first()
                if not team:
                    raise HTTPException(status_code=400, detail="Invalid controlling_team_id: team not found")
                setattr(sector, field, uuid.UUID(value))
            elif hasattr(sector, field):
                setattr(sector, field, value)
        
        # Update last_updated timestamp
        sector.last_updated = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(sector)
        
        logger.info(f"Admin {current_admin.username} updated sector {sector.name} (ID: {sector.sector_id})")
        
        return {
            "message": "Sector updated successfully",
            "sector_id": str(sector.id),
            "sector_number": sector.sector_id,
            "name": sector.name
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating sector {sector_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update sector: {str(e)}")

@router.post("/sectors/{sector_id}/planet", response_model=Dict[str, Any])
async def create_planet_in_sector(
    sector_id: str,
    planet_data: PlanetCreateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a planet in the specified sector"""
    try:
        # Find the sector - try UUID first, fallback to integer sector_id
        sector = None
        try:
            # Try as UUID first (UUIDs are 36 characters with hyphens)
            sector_uuid = uuid.UUID(sector_id)
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            # If UUID parsing fails, try as integer sector_id
            try:
                sector_int = int(sector_id)
                sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Check if sector already has a planet
        existing_planet = db.query(Planet).filter(
            Planet.sector_uuid == sector.id
        ).first()
        
        if existing_planet:
            raise HTTPException(status_code=400, detail="Sector already has a planet")
        
        # Import and validate planet type
        from src.models.planet import Planet, PlanetType, PlanetStatus
        
        try:
            planet_type = PlanetType(planet_data.type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid planet type: {planet_data.type}")
        
        # Create new planet
        new_planet = Planet(
            name=planet_data.name,
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            type=planet_type,
            status=PlanetStatus.UNINHABITABLE,
            size=planet_data.size,
            position=planet_data.position,
            gravity=planet_data.gravity,
            temperature=planet_data.temperature,
            water_coverage=planet_data.water_coverage,
            habitability_score=planet_data.habitability_score,
            resource_richness=planet_data.resource_richness
        )
        
        db.add(new_planet)
        db.commit()
        db.refresh(new_planet)
        
        logger.info(f"Admin {current_admin.username} created planet {new_planet.name} in sector {sector.sector_id}")
        
        return {
            "message": "Planet created successfully",
            "planet_id": str(new_planet.id),
            "planet_name": new_planet.name,
            "sector_id": str(sector.id),
            "sector_number": sector.sector_id
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating planet in sector {sector_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create planet: {str(e)}")

@router.post("/sectors/{sector_id}/port", response_model=Dict[str, Any])
async def create_port_in_sector(
    sector_id: str,
    station_data: StationCreateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a port in the specified sector"""
    try:
        # Find the sector - try UUID first, fallback to integer sector_id
        sector = None
        try:
            # Try as UUID first (UUIDs are 36 characters with hyphens)
            sector_uuid = uuid.UUID(sector_id)
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            # If UUID parsing fails, try as integer sector_id
            try:
                sector_int = int(sector_id)
                sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Check if sector already has a port
        existing_station = db.query(Station).filter(
            Station.sector_uuid == sector.id
        ).first()
        
        if existing_station:
            raise HTTPException(status_code=400, detail="Sector already has a port")
        
        # Import and validate enums
        from src.models.station import Station, StationClass, StationType, StationStatus

        try:
            port_class = StationClass(station_data.station_class)
            port_type = StationType(station_data.type)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid port class or type: {str(e)}")

        # Create new port
        new_port = Station(
            name=station_data.name,
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            station_class=port_class,
            type=port_type,
            status=StationStatus.OPERATIONAL,
            size=station_data.size,
            faction_affiliation=station_data.faction_affiliation,
            trade_volume=station_data.trade_volume,
            market_volatility=station_data.market_volatility
        )
        
        db.add(new_port)
        db.commit()
        db.refresh(new_port)
        
        logger.info(f"Admin {current_admin.username} created port {new_port.name} in sector {sector.sector_id}")
        
        return {
            "message": "Station created successfully",
            "station_id": str(new_port.id),
            "station_name": new_port.name,
            "sector_id": str(sector.id),
            "sector_number": sector.sector_id
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating port in sector {sector_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create port: {str(e)}")

@router.get("/sectors/{sector_id}/planet")
async def get_sector_planet(
    sector_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get detailed planet information for a specific sector"""
    logger.info(f"Getting planet for sector {sector_id}")
    try:
        # Find the sector - try UUID first, fallback to integer sector_id
        sector = None
        try:
            # Try as UUID first (UUIDs are 36 characters with hyphens)
            sector_uuid = uuid.UUID(sector_id)
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            # If UUID parsing fails, try as integer sector_id
            try:
                sector_int = int(sector_id)
                sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Find the planet in this sector
        logger.info(f"Looking for planet in sector with id: {sector.id}")
        planet = db.query(Planet).filter(Planet.sector_uuid == sector.id).first()
        logger.info(f"Found planet: {planet}")
        
        if not planet:
            return {"has_planet": False, "planet": None}
        
        # Get owner information if planet is owned
        owner_name = None
        if planet.owner_id:
            owner = db.query(Player).join(User).filter(Player.id == planet.owner_id).first()
            if owner:
                owner_name = owner.user.username
        
        return {
            "has_planet": True,
            "planet": {
                "id": str(planet.id),
                "name": planet.name,
                "type": planet.type.value if planet.type else None,
                "status": planet.status.value if planet.status else None,
                "size": planet.size,
                "position": planet.position,
                "gravity": planet.gravity,
                "temperature": planet.temperature,
                "water_coverage": planet.water_coverage,
                "habitability_score": planet.habitability_score,
                "radiation_level": planet.radiation_level,
                "resource_richness": planet.resource_richness,
                "population": planet.population,
                "max_population": planet.max_population,
                "defense_level": planet.defense_level,
                "owner_id": str(planet.owner_id) if planet.owner_id else None,
                "owner_name": owner_name,
                "colonized_at": planet.colonized_at.isoformat() if planet.colonized_at else None,
                "created_at": planet.created_at.isoformat()
            }
        }
        
    except ValueError as ve:
        logger.error(f"Validation error getting planet for sector {sector_id}: {ve}")
        raise HTTPException(status_code=422, detail=f"Validation error: {str(ve)}")
    except Exception as e:
        logger.error(f"Error getting planet for sector {sector_id}: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get sector planet: {str(e)}")

@router.get("/sectors/{sector_id}/port")
async def get_sector_port(
    sector_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get detailed port information for a specific sector"""
    try:
        # Find the sector - try UUID first, fallback to integer sector_id
        sector = None
        try:
            # Try as UUID first (UUIDs are 36 characters with hyphens)
            sector_uuid = uuid.UUID(sector_id)
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            # If UUID parsing fails, try as integer sector_id
            try:
                sector_int = int(sector_id)
                sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Find the port in this sector
        station = db.query(Station).filter(Station.sector_uuid == sector.id).first()

        if not station:
            return {"has_station": False, "station": None}
        
        # Get owner information if port is owned
        owner_name = None
        if station.owner_id:
            owner = db.query(Player).join(User).filter(Player.id == station.owner_id).first()
            if owner:
                owner_name = owner.user.username
        
        return {
            "has_port": True,
            "station": {
                "id": str(station.id),
                "name": station.name,
                "station_class": station.station_class.value,
                "type": station.type.value,
                "status": station.status.value,
                "size": station.size,
                "faction_affiliation": station.faction_affiliation,
                "trade_volume": station.trade_volume,
                "market_volatility": station.market_volatility,
                "owner_id": str(station.owner_id) if station.owner_id else None,
                "owner_name": owner_name,
                "created_at": station.created_at.isoformat()
            }
        }
        
    except ValueError as ve:
        logger.error(f"Validation error getting port for sector {sector_id}: {ve}")
        raise HTTPException(status_code=422, detail=f"Validation error: {str(ve)}")
    except Exception as e:
        logger.error(f"Error getting port for sector {sector_id}: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get sector port: {str(e)}")

@router.get("/sectors/{sector_id}/warp-tunnels")
async def get_sector_warp_tunnels(
    sector_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all warp tunnels for a specific sector (outgoing, incoming, and bidirectional)"""
    try:
        # Find the sector - try UUID first, fallback to integer sector_id
        sector = None
        try:
            sector_uuid = uuid.UUID(sector_id)
            sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            try:
                sector_int = int(sector_id)
                sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")

        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")

        # Get all tunnels where this sector is origin OR destination
        outgoing_tunnels = db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == sector.id
        ).all()

        incoming_tunnels = db.query(WarpTunnel).filter(
            WarpTunnel.destination_sector_id == sector.id,
            WarpTunnel.is_bidirectional == False  # Only show one-way incoming
        ).all()

        # Build response data
        def format_tunnel(tunnel, is_outgoing=True):
            """Format tunnel data for response"""
            # Determine which sector to show (the "other" end)
            if is_outgoing:
                other_sector = tunnel.destination_sector
                direction = "outgoing"
            else:
                other_sector = tunnel.origin_sector
                direction = "incoming"

            other_sector_num = other_sector.sector_id if other_sector else None
            other_sector_name = other_sector.name if other_sector else "Unknown"
            other_region_name = other_sector.region.name if other_sector and other_sector.region else "Unknown"

            return {
                "id": str(tunnel.id),
                "name": tunnel.name,
                "direction": direction,
                "other_sector_id": other_sector_num,
                "other_sector_name": other_sector_name,
                "other_region_name": other_region_name,
                "origin_sector_id": tunnel.origin_sector.sector_id if tunnel.origin_sector else None,
                "destination_sector_id": tunnel.destination_sector.sector_id if tunnel.destination_sector else None,
                "type": tunnel.type.value,
                "status": tunnel.status.value,
                "stability": tunnel.stability,
                "is_bidirectional": tunnel.is_bidirectional,
                "turn_cost": tunnel.turn_cost,
                "energy_cost": tunnel.energy_cost,
                "is_public": tunnel.is_public,
                "total_traversals": tunnel.total_traversals,
                "created_at": tunnel.created_at.isoformat()
            }

        outgoing_data = [format_tunnel(t, is_outgoing=True) for t in outgoing_tunnels]
        incoming_data = [format_tunnel(t, is_outgoing=False) for t in incoming_tunnels]

        return {
            "sector_id": sector.sector_id,
            "sector_name": sector.name,
            "sector_uuid": str(sector.id),
            "outgoing": outgoing_data,
            "incoming": incoming_data,
            "total_tunnels": len(outgoing_data) + len(incoming_data)
        }

    except ValueError as ve:
        logger.error(f"Validation error getting warp tunnels for sector {sector_id}: {ve}")
        raise HTTPException(status_code=422, detail=f"Validation error: {str(ve)}")
    except Exception as e:
        logger.error(f"Error getting warp tunnels for sector {sector_id}: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get sector warp tunnels: {str(e)}")

class WarpTunnelCreateRequest(BaseModel):
    """Request model for creating a new warp tunnel"""
    name: str = Field(..., max_length=100, description="Tunnel name")
    destination_sector_id: int = Field(..., description="Destination sector number")
    type: str = Field("STANDARD", description="Tunnel type (NATURAL, ARTIFICIAL, STANDARD, etc.)")
    is_bidirectional: bool = Field(True, description="Can be used in both directions")
    turn_cost: int = Field(5, ge=1, le=100, description="Turns required to traverse")
    stability: float = Field(1.0, ge=0.0, le=1.0, description="Stability rating 0-1")
    is_public: bool = Field(True, description="Whether anyone can use it")

class WarpTunnelUpdateRequest(BaseModel):
    """Request model for updating a warp tunnel"""
    name: Optional[str] = Field(None, max_length=100)
    type: Optional[str] = None
    status: Optional[str] = None
    is_bidirectional: Optional[bool] = None
    turn_cost: Optional[int] = Field(None, ge=1, le=100)
    stability: Optional[float] = Field(None, ge=0.0, le=1.0)
    energy_cost: Optional[int] = Field(None, ge=0)
    is_public: Optional[bool] = None

@router.post("/sectors/{sector_id}/warp-tunnels")
async def create_warp_tunnel(
    sector_id: str,
    tunnel_data: WarpTunnelCreateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new warp tunnel from this sector to another"""
    try:
        # Find origin sector
        origin_sector = None
        try:
            sector_uuid = uuid.UUID(sector_id)
            origin_sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
        except ValueError:
            try:
                sector_int = int(sector_id)
                origin_sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")

        if not origin_sector:
            raise HTTPException(status_code=404, detail="Origin sector not found")

        # Find destination sector (by sector_id number within same region)
        dest_sector = db.query(Sector).filter(
            Sector.sector_id == tunnel_data.destination_sector_id,
            Sector.region_id == origin_sector.region_id  # Must be same region
        ).first()

        if not dest_sector:
            raise HTTPException(
                status_code=404,
                detail=f"Destination sector {tunnel_data.destination_sector_id} not found in same region"
            )

        # Validation: no self-loops
        if origin_sector.id == dest_sector.id:
            raise HTTPException(status_code=400, detail="Cannot create tunnel from sector to itself")

        # Check for duplicate tunnels
        existing = db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == origin_sector.id,
            WarpTunnel.destination_sector_id == dest_sector.id
        ).first()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Tunnel already exists from sector {origin_sector.sector_id} to {dest_sector.sector_id}"
            )

        # Import WarpTunnelType and WarpTunnelStatus enums
        from src.models.warp_tunnel import WarpTunnelType, WarpTunnelStatus

        # Validate and convert type
        try:
            tunnel_type = WarpTunnelType[tunnel_data.type.upper()]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid tunnel type: {tunnel_data.type}")

        # Create the tunnel
        new_tunnel = WarpTunnel(
            name=tunnel_data.name,
            origin_sector_id=origin_sector.id,
            destination_sector_id=dest_sector.id,
            type=tunnel_type,
            status=WarpTunnelStatus.ACTIVE,
            is_bidirectional=tunnel_data.is_bidirectional,
            stability=tunnel_data.stability,
            turn_cost=tunnel_data.turn_cost,
            energy_cost=0,
            is_public=tunnel_data.is_public
        )

        db.add(new_tunnel)
        db.commit()
        db.refresh(new_tunnel)

        return {
            "success": True,
            "message": f"Warp tunnel created from sector {origin_sector.sector_id} to {dest_sector.sector_id}",
            "tunnel": {
                "id": str(new_tunnel.id),
                "name": new_tunnel.name,
                "origin_sector_id": origin_sector.sector_id,
                "destination_sector_id": dest_sector.sector_id,
                "type": new_tunnel.type.value,
                "is_bidirectional": new_tunnel.is_bidirectional,
                "turn_cost": new_tunnel.turn_cost
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating warp tunnel: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to create warp tunnel: {str(e)}")

@router.put("/warp-tunnels/{tunnel_id}")
async def update_warp_tunnel(
    tunnel_id: str,
    tunnel_data: WarpTunnelUpdateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update an existing warp tunnel"""
    try:
        # Find the tunnel
        try:
            tunnel_uuid = uuid.UUID(tunnel_id)
            tunnel = db.query(WarpTunnel).filter(WarpTunnel.id == tunnel_uuid).first()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tunnel ID format")

        if not tunnel:
            raise HTTPException(status_code=404, detail="Warp tunnel not found")

        # Import enums
        from src.models.warp_tunnel import WarpTunnelType, WarpTunnelStatus

        # Update provided fields
        update_data = tunnel_data.dict(exclude_unset=True)

        for field, value in update_data.items():
            if field == "type" and value:
                try:
                    tunnel.type = WarpTunnelType[value.upper()]
                except KeyError:
                    raise HTTPException(status_code=400, detail=f"Invalid tunnel type: {value}")
            elif field == "status" and value:
                try:
                    tunnel.status = WarpTunnelStatus[value.upper()]
                except KeyError:
                    raise HTTPException(status_code=400, detail=f"Invalid tunnel status: {value}")
            else:
                setattr(tunnel, field, value)

        db.commit()
        db.refresh(tunnel)

        return {
            "success": True,
            "message": "Warp tunnel updated successfully",
            "tunnel": {
                "id": str(tunnel.id),
                "name": tunnel.name,
                "type": tunnel.type.value,
                "status": tunnel.status.value,
                "is_bidirectional": tunnel.is_bidirectional,
                "turn_cost": tunnel.turn_cost,
                "stability": tunnel.stability
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating warp tunnel {tunnel_id}: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to update warp tunnel: {str(e)}")

@router.delete("/warp-tunnels/{tunnel_id}")
async def delete_warp_tunnel(
    tunnel_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a warp tunnel"""
    try:
        # Find the tunnel
        try:
            tunnel_uuid = uuid.UUID(tunnel_id)
            tunnel = db.query(WarpTunnel).filter(WarpTunnel.id == tunnel_uuid).first()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tunnel ID format")

        if not tunnel:
            raise HTTPException(status_code=404, detail="Warp tunnel not found")

        # Store info for response
        tunnel_info = {
            "name": tunnel.name,
            "origin_sector_id": tunnel.origin_sector.sector_id if tunnel.origin_sector else None,
            "destination_sector_id": tunnel.destination_sector.sector_id if tunnel.destination_sector else None,
            "is_bidirectional": tunnel.is_bidirectional
        }

        # Delete the tunnel
        db.delete(tunnel)
        db.commit()

        return {
            "success": True,
            "message": f"Warp tunnel '{tunnel_info['name']}' deleted successfully",
            "was_bidirectional": tunnel_info["is_bidirectional"]
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting warp tunnel {tunnel_id}: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to delete warp tunnel: {str(e)}")

@router.get("/warp-tunnels", response_model=Dict[str, Any])
async def get_warp_tunnels(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filter_type: Optional[str] = None,
    filter_status: Optional[str] = None,
    filter_origin_sector: Optional[int] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Simple warp tunnels endpoint that redirects to comprehensive endpoint"""
    return await get_warp_tunnels_comprehensive(
        page=page,
        limit=limit,
        filter_type=filter_type,
        filter_status=filter_status,
        filter_origin_sector=filter_origin_sector,
        current_admin=current_admin,
        db=db
    )

# Station CRUD Operations

class StationUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    port_class: Optional[str] = None  # Station class enum name (e.g., "CLASS_1")
    trade_volume: Optional[int] = Field(None, ge=0)
    max_capacity: Optional[int] = Field(None, ge=0)
    security_level: Optional[int] = Field(None, ge=0, le=100)
    docking_fee: Optional[int] = Field(None, ge=0)
    owner_name: Optional[str] = None

@router.patch("/ports/{station_id}", response_model=Dict[str, Any])
async def update_port(
    station_id: str,
    station_data: StationUpdateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update a port's properties"""
    try:
        # Find the port by ID
        station = db.query(Station).filter(Station.id == station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        # Update fields that were provided
        update_data = station_data.dict(exclude_unset=True)

        for field, value in update_data.items():
            if field == "port_class" and value:
                # Convert string enum name (e.g. "CLASS_1") to StationClass
                from src.models.station import StationClass
                try:
                    station_class_enum = getattr(StationClass, value)
                    setattr(station, "station_class", station_class_enum)
                except AttributeError:
                    raise HTTPException(status_code=400, detail=f"Invalid port class: {value}")
            elif field == "owner_name":
                # Handle owner assignment
                if value:
                    # Find player by username
                    player = db.query(Player).join(User).filter(User.username == value).first()
                    if player:
                        station.owner_id = player.id
                    else:
                        raise HTTPException(status_code=404, detail=f"Player '{value}' not found")
                else:
                    # Clear owner
                    station.owner_id = None
            else:
                # Direct field update
                setattr(station, field, value)

        db.commit()
        
        return {
            "message": "Station updated successfully",
            "station_id": str(station.id),
            "updated_fields": list(update_data.keys())
        }
        
    except Exception as e:
        logger.error(f"Error updating port {station_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update port: {str(e)}")

@router.delete("/ports/{station_id}", response_model=Dict[str, Any])
async def delete_port(
    station_id: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a port"""
    try:
        # Find the port by ID
        station = db.query(Station).filter(Station.id == station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        station_name = station.name

        # Delete the port
        db.delete(station)
        db.commit()
        
        return {
            "message": f"Station '{station_name}' deleted successfully",
            "station_id": station_id
        }
        
    except Exception as e:
        logger.error(f"Error deleting port {station_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete port: {str(e)}")

@router.post("/ports", response_model=Dict[str, Any])
async def create_port(
    port_data: Dict[str, Any],
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new port"""
    try:
        # Import and validate enums
        from src.models.station import Station, StationClass, StationType, StationStatus

        # Validate required fields
        if not port_data.get("name"):
            raise HTTPException(status_code=400, detail="Station name is required")
        if not port_data.get("sector_id"):
            raise HTTPException(status_code=400, detail="Sector ID is required")
        
        # Find the sector
        sector_id = port_data["sector_id"]
        sector = None
        try:
            # Try as integer sector_id first
            sector_int = int(sector_id)
            sector = db.query(Sector).filter(Sector.sector_id == sector_int).first()
        except ValueError:
            # Try as UUID
            try:
                sector_uuid = uuid.UUID(sector_id)
                sector = db.query(Sector).filter(Sector.id == sector_uuid).first()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid sector ID format")
        
        if not sector:
            raise HTTPException(status_code=404, detail="Sector not found")
        
        # Check if sector already has a port
        existing_station = db.query(Station).filter(Station.sector_uuid == sector.id).first()
        if existing_station:
            raise HTTPException(status_code=400, detail="Sector already has a port")
        
        # Parse port class
        try:
            port_class_str = port_data.get("station_class", "CLASS_1")
            port_class = getattr(StationClass, port_class_str)
        except AttributeError:
            raise HTTPException(status_code=400, detail=f"Invalid port class: {port_class_str}")

        # Handle owner assignment
        owner_id = None
        if port_data.get("owner_name"):
            player = db.query(Player).join(User).filter(User.username == port_data["owner_name"]).first()
            if player:
                owner_id = player.id
            else:
                raise HTTPException(status_code=404, detail=f"Player '{port_data['owner_name']}' not found")
        
        # Create the port with all required fields
        new_port = Station(
            name=port_data["name"],
            sector_id=sector.sector_id,
            sector_uuid=sector.id,
            station_class=port_class,
            type=StationType.TRADING,  # Default to trading
            status=StationStatus.OPERATIONAL,
            trade_volume=port_data.get("trade_volume", 1000),
            size=port_data.get("size", 5),
            owner_id=owner_id,
            # Set default values for required fields
            faction_affiliation=port_data.get("faction_affiliation", None),
            market_volatility=port_data.get("market_volatility", 50)
        )
        
        # Update commodity stock levels based on port class
        new_port.update_commodity_trading_flags()
        new_port.update_commodity_stock_levels()
        
        db.add(new_port)
        db.commit()
        
        return {
            "message": "Station created successfully",
            "station_id": str(new_port.id),
            "station_name": new_port.name,
            "sector_id": sector.sector_id
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like "sector already has a port")
        db.rollback()
        raise
    except Exception as e:
        logger.error(f"Error creating port: {e}")
        logger.error(f"Station data: {port_data}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create port: {str(e)}")

# =============================================================================
# AI TRADING INTELLIGENCE ADMIN ENDPOINTS
# =============================================================================

@router.get("/ai/models", response_model=List[Dict[str, Any]])
async def get_ai_models(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI models status for admin dashboard"""
    try:
        # No AI model registry exists in the system; there is no table or
        # service tracking trained models, accuracy, or training schedules.
        # Return an empty list rather than fabricated model status.
        models: List[Dict[str, Any]] = []

        logger.info(f"Admin {current_admin.username} requested AI models data")
        return models
        
    except Exception as e:
        logger.error(f"Error getting AI models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI models: {str(e)}")

@router.get("/ai/predictions/accuracy", response_model=List[Dict[str, Any]])
async def get_ai_prediction_accuracy(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI prediction accuracy by commodity for admin dashboard"""
    try:
        # No prediction accuracy tracking exists; predictions are not logged
        # or scored against outcomes anywhere in the system. Return an empty
        # list rather than fabricated per-commodity accuracy figures.
        commodities: List[Dict[str, Any]] = []

        logger.info(f"Admin {current_admin.username} requested AI prediction accuracy data")
        return commodities
        
    except Exception as e:
        logger.error(f"Error getting AI prediction accuracy: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI prediction accuracy: {str(e)}")

@router.get("/ai/profiles", response_model=List[Dict[str, Any]])
async def get_ai_player_profiles(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI player profiles for admin dashboard"""
    try:
        # Get actual player data; only fields read 1:1 from Player/User rows
        # are returned. riskTolerance/tradingPatterns/aiEngagement/
        # profitImprovement were hash-synthesized scores with no backing data
        # and have been removed — no AI profiling telemetry exists yet.
        players = db.query(Player).join(User).limit(10).all()

        profiles = []
        for player in players:
            profiles.append({
                "playerId": str(player.id),
                "playerName": player.user.username,
                "lastActive": (player.last_game_login or player.user.created_at).isoformat()
            })
        
        logger.info(f"Admin {current_admin.username} requested AI player profiles")
        return profiles
        
    except Exception as e:
        logger.error(f"Error getting AI player profiles: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI player profiles: {str(e)}")

@router.get("/ai/metrics", response_model=Dict[str, Any])
async def get_ai_system_metrics(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI system metrics for admin dashboard"""
    try:
        # Get actual player and activity data
        total_players = db.query(Player).count()
        day_ago = datetime.utcnow() - timedelta(days=1)
        active_players = db.query(Player).filter(Player.last_game_login >= day_ago).count()
        
        # Honest metrics: no AI prediction engine, model registry, or job queue
        # exists yet — only real ARIA interaction counts are reportable.
        # (De-mocked: the previous hardcoded 3247/79.8/67.3/12/45 figures were
        # fabrications presented as live telemetry.)
        total_aria_interactions = db.query(
            func.coalesce(func.sum(Player.aria_total_interactions), 0)
        ).scalar() or 0
        aria_active_profiles = db.query(Player).filter(
            Player.aria_total_interactions > 0
        ).count()

        metrics = {
            "totalPredictions": None,   # no prediction engine exists
            "avgAccuracy": None,        # no model accuracy telemetry exists
            "activeProfiles": aria_active_profiles,  # players with ARIA activity
            "recommendationAcceptance": None,  # no recommendation telemetry exists
            "modelHealth": None,        # no model registry exists
            "queuedJobs": None,         # no job queue exists
            "processingRate": None,     # no processing pipeline exists
            "totalAriaInteractions": int(total_aria_interactions),
        }
        
        logger.info(f"Admin {current_admin.username} requested AI system metrics")
        return metrics
        
    except Exception as e:
        logger.error(f"Error getting AI system metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI system metrics: {str(e)}")

@router.post("/ai/models/{model_id}/{action}", response_model=Dict[str, Any])
async def ai_model_action(
    model_id: str,
    action: str,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Take action on AI model (start, stop, train)"""
    if action not in ["start", "stop", "train"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    # No AI model registry exists in the system (see GET /ai/models), so
    # there is nothing to start/stop/train. The previous implementation
    # returned a fabricated success message without doing anything.
    logger.info(f"Admin {current_admin.username} attempted {action} on AI model {model_id} (not implemented)")
    raise HTTPException(
        status_code=501,
        detail="No AI model registry exists; model actions are not implemented"
    )

@router.get("/ai/predictions", response_model=List[Dict[str, Any]])
async def get_ai_predictions(
    timeframe: str = Query("1h", description="Prediction timeframe"),
    resource: Optional[str] = Query(None, description="Filter by resource"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI price predictions for admin dashboard"""
    try:
        # No price prediction engine exists; nothing in the system generates,
        # stores, or scores price forecasts (see GET /ai/models — there is no
        # model registry either). Return an empty list rather than fabricated
        # predictions.
        predictions: List[Dict[str, Any]] = []

        logger.info(f"Admin {current_admin.username} requested AI predictions")
        return predictions

    except Exception as e:
        logger.error(f"Error getting AI predictions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI predictions: {str(e)}")


@router.get("/ai/route-optimization", response_model=Dict[str, Any])
async def get_ai_route_optimization_data(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI route optimization data for admin dashboard"""
    try:
        # No route optimization engine exists; nothing in the system computes,
        # tracks, or stores optimized routes or their outcomes. Return an
        # empty structure (with null stats — zeros would themselves be
        # fabricated metrics) rather than invented routes.
        data = {
            "active_optimizations": [],
            "optimization_stats": None
        }

        logger.info(f"Admin {current_admin.username} requested AI route optimization data")
        return data

    except Exception as e:
        logger.error(f"Error getting AI route optimization data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI route optimization data: {str(e)}")

@router.get("/ai/behavior-analytics", response_model=Dict[str, Any])
async def get_ai_behavior_analytics(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get AI behavior analytics data for admin dashboard"""
    try:
        # No behavior analytics pipeline exists; player trading patterns,
        # AI engagement, and insights are not classified or tracked anywhere
        # in the system. The previous payload apportioned the real player
        # count across invented pattern buckets with fabricated percentages.
        # Return an empty structure (null metrics — zeros would themselves be
        # fabricated) rather than synthesized analytics.
        data = {
            "player_patterns": [],
            "engagement_metrics": None,
            "recent_insights": []
        }

        logger.info(f"Admin {current_admin.username} requested AI behavior analytics")
        return data

    except Exception as e:
        logger.error(f"Error getting AI behavior analytics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get AI behavior analytics: {str(e)}")