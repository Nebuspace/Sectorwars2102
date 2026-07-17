from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text, func, desc, or_
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timezone
import random
import math
import logging

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.models.player import Player
from src.models.ship import Ship
from src.models.galaxy import Galaxy
from src.models.region import Region
from src.models.zone import Zone
from src.models.cluster import Cluster
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel
from src.models.station import Station
from src.models.planet import Planet
from src.models.team import Team
from src.models.game_event import GameEvent, EventEffect, EventParticipation, EventType, EventStatus
from src.schemas.user import UserAdminResponse

# Request schemas for universe management
class GalaxyGenerateRequest(BaseModel):
    name: str
    num_sectors: int
    config: Optional[dict] = None
    federation_percentage: Optional[int] = 25
    border_percentage: Optional[int] = 35
    frontier_percentage: Optional[int] = 40

class SectorAddRequest(BaseModel):
    num_sectors: int
    config: Optional[dict] = None

class WarpTunnelCreateRequest(BaseModel):
    source_sector_id: int
    target_sector_id: int
    stability: Optional[float] = 0.75

# Event management schemas
class QuickEventCreateRequest(BaseModel):
    """Simplified event creation for admin dashboard quick-actions."""
    title: str
    description: str
    event_type: str = "economic"  # economic, combat, exploration, seasonal, emergency, story
    duration_hours: int = 24
    affected_regions: Optional[List[str]] = None  # None = global
    effects: Optional[List[Dict[str, Any]]] = None
    auto_start: bool = False

class EventUpdateRequest(BaseModel):
    """Partial update for an existing event."""
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # scheduled, active, completed, cancelled, paused
    end_time: Optional[datetime] = None

# Zone response schemas
class ZoneResponse(BaseModel):
    id: str
    region_id: str
    name: str
    zone_type: str
    start_sector: int
    end_sector: int
    sector_count: int  # Calculated from range
    policing_level: int
    danger_rating: int
    created_at: str
    # Optional aggregated stats
    actual_sector_count: Optional[int] = None
    avg_security_level: Optional[float] = None

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/users", response_model=dict)
async def get_all_users(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all users for admin panel (excludes soft-deleted accounts)"""
    users = db.query(User).filter(User.deleted == False).all()

    # Map to response model
    user_list = [
        {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "deleted": user.deleted,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "verified": True  # Users are verified by default in this system
        }
        for user in users
    ]
    
    return {"users": user_list}

@router.get("/players", response_model=dict)
async def get_all_players(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all player accounts for admin panel"""
    try:
        players = db.query(Player).all()
        
        # Map to response model with real counts
        player_list = []
        for player in players:
            try:
                # Get username safely - handle case where user relationship might be missing
                username = "Unknown"
                try:
                    if player.user:
                        username = player.user.username
                    else:
                        # Try to load user separately if relationship is lazy-loaded
                        user = db.query(User).filter(User.id == player.user_id).first()
                        if user:
                            username = user.username
                except Exception as e:
                    logger.warning(f"Failed to resolve username for player {player.id}: {e}")
                    username = f"User-{player.user_id}"

                # Get real ship count for this player
                try:
                    from src.models.ship import Ship
                    ships_count = db.query(Ship).filter(Ship.owner_id == player.id).count()
                except Exception as e:
                    logger.warning(f"Failed to get ship count for player {player.id}: {e}")
                    ships_count = 0

                # Get real planet count for this player
                try:
                    planets_count = db.query(Planet).filter(Planet.owner_id == player.id).count()
                except Exception as e:
                    logger.warning(f"Failed to get planet count for player {player.id}: {e}")
                    planets_count = 0

                # Get team info safely
                team_id = None
                try:
                    team_id = str(player.team_id) if player.team_id else None
                except Exception as e:
                    logger.warning(f"Failed to get team_id for player {player.id}: {e}")

                # Get email and user data safely
                email = "unknown@example.com"
                created_at = None
                last_login = None
                try:
                    if player.user:
                        email = player.user.email
                        created_at = player.user.created_at.isoformat() if player.user.created_at else None
                        last_login = player.user.last_login.isoformat() if player.user.last_login else None
                except Exception as e:
                    logger.warning(f"Failed to get user data for player {player.id}: {e}")

                # Get ports count
                ports_count = 0
                try:
                    from src.models.station import Station
                    ports_count = db.query(Station).filter(Station.owner_id == player.id).count()
                except Exception as e:
                    logger.warning(f"Failed to get ports count for player {player.id}: {e}")

                # Calculate total asset value (simplified)
                total_asset_value = getattr(player, 'credits', 0)

                # Determine status
                is_active = getattr(player, 'is_active', True)
                status = "active" if is_active else "inactive"

                player_list.append({
                    "id": str(player.id),
                    "user_id": str(player.user_id),
                    "username": username,
                    "email": email,
                    "credits": getattr(player, 'credits', 0),
                    "turns": getattr(player, 'turns', 0),
                    "last_game_login": player.last_game_login.isoformat() if getattr(player, 'last_game_login', None) else None,
                    "current_sector_id": getattr(player, 'current_sector_id', 1),
                    "current_region_id": str(player.current_region_id) if getattr(player, 'current_region_id', None) else None,
                    "current_ship_id": str(player.current_ship_id) if getattr(player, 'current_ship_id', None) else None,
                    "ships_count": ships_count,
                    "planets_count": planets_count,
                    "ports_count": ports_count,
                    "team_id": team_id,
                    "is_active": is_active,
                    "status": status,
                    "created_at": created_at,
                    "last_login": last_login,
                    # Assets summary
                    "assets": {
                        "ships_count": ships_count,
                        "planets_count": planets_count,
                        "ports_count": ports_count,
                        "total_value": total_asset_value
                    },
                    # Activity summary (defaults for now)
                    "activity": {
                        "last_login": last_login,
                        "session_count_today": 0,
                        "actions_today": 0,
                        "total_trade_volume": 0,
                        "combat_rating": 0,
                        "suspicious_activity": False
                    },
                    # ARIA summary (empty for now - will populate when data collection is active)
                    "aria": None
                })
            except Exception as e:
                logger.error(f"Error processing player {player.id}: {e}")
                # Add minimal player info even if detailed processing fails
                player_list.append({
                    "id": str(player.id),
                    "user_id": str(getattr(player, 'user_id', 'unknown')),
                    "username": f"Player-{player.id}",
                    "email": "unknown@example.com",
                    "credits": 0,
                    "turns": 0,
                    "last_game_login": None,
                    "current_sector_id": 1,
                    "current_ship_id": None,
                    "ships_count": 0,
                    "planets_count": 0,
                    "ports_count": 0,
                    "team_id": None,
                    "is_active": True,
                    "status": "active",
                    "created_at": None,
                    "last_login": None,
                    "assets": {
                        "ships_count": 0,
                        "planets_count": 0,
                        "ports_count": 0,
                        "total_value": 0
                    },
                    "activity": {
                        "last_login": None,
                        "session_count_today": 0,
                        "actions_today": 0,
                        "total_trade_volume": 0,
                        "combat_rating": 0,
                        "suspicious_activity": False
                    },
                    "aria": None
                })
        
        return {"players": player_list}

    except Exception as e:
        logger.error(f"Error getting all players: {e}")
        # Return empty list if query fails completely
        return {"players": []}

@router.get("/regions", response_model=dict)
async def get_all_regions(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all regions for admin panel"""
    regions = db.query(Region).all()

    region_list = [{
        "id": str(region.id),
        "name": region.name,
        "display_name": region.display_name,
        "region_type": region.region_type,
        "total_sectors": region.total_sectors,
        "status": region.status,
        "subscription_tier": region.subscription_tier,
        "starting_credits": region.starting_credits,
        "governance_type": region.governance_type,
        "tax_rate": float(region.tax_rate)
    } for region in regions]

    return {"regions": region_list}


@router.get("/regions/{region_id}/zones", response_model=dict)
async def get_region_zones(
    region_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Get all zones for a specific region (admin only)

    Returns zones with sector statistics:
    - Central Nexus: 1 zone ("The Expanse")
    - Terran Space: 3 zones (Federation/Border/Frontier)
    - Player Regions: 3 zones (Federation/Border/Frontier)
    """
    # Verify region exists
    region = db.query(Region).filter(Region.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail=f"Region {region_id} not found")

    # Get all zones for this region
    zones = db.query(Zone).filter(Zone.region_id == region_id).order_by(Zone.start_sector).all()

    zone_list = []
    for zone in zones:
        # Count actual sectors in this zone
        sector_count_query = db.query(Sector).filter(Sector.zone_id == zone.id).count()

        # Calculate average security level
        from sqlalchemy import func
        avg_security = db.query(func.avg(Sector.security_level)).filter(Sector.zone_id == zone.id).scalar()

        zone_data = {
            "id": str(zone.id),
            "region_id": str(zone.region_id),
            "name": zone.name,
            "zone_type": zone.zone_type,
            "start_sector": zone.start_sector,
            "end_sector": zone.end_sector,
            "sector_count": zone.end_sector - zone.start_sector + 1,  # Theoretical count
            "policing_level": zone.policing_level,
            "danger_rating": zone.danger_rating,
            "created_at": zone.created_at.isoformat() if zone.created_at else None,
            "actual_sector_count": sector_count_query,  # Actual sectors with this zone_id
            "avg_security_level": float(avg_security) if avg_security else None
        }
        zone_list.append(zone_data)

    return {
        "region_id": str(region_id),
        "region_name": region.display_name,
        "region_type": region.region_type,
        "zones": zone_list,
        "total_zones": len(zone_list)
    }


@router.get("/regions/{region_id}/zones/{zone_id}", response_model=ZoneResponse)
async def get_zone_details(
    region_id: str,
    zone_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific zone (admin only)

    Validates that the zone belongs to the requested region.
    """
    # Verify region exists
    region = db.query(Region).filter(Region.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail=f"Region {region_id} not found")

    # Get zone and verify it belongs to this region
    zone = db.query(Zone).filter(
        Zone.id == zone_id,
        Zone.region_id == region_id  # Security: ensure zone belongs to region
    ).first()

    if not zone:
        raise HTTPException(
            status_code=404,
            detail=f"Zone {zone_id} not found in region {region_id}"
        )

    # Count actual sectors
    sector_count_query = db.query(Sector).filter(Sector.zone_id == zone.id).count()

    # Calculate average security level
    from sqlalchemy import func
    avg_security = db.query(func.avg(Sector.security_level)).filter(Sector.zone_id == zone.id).scalar()

    return ZoneResponse(
        id=str(zone.id),
        region_id=str(zone.region_id),
        name=zone.name,
        zone_type=zone.zone_type,
        start_sector=zone.start_sector,
        end_sector=zone.end_sector,
        sector_count=zone.end_sector - zone.start_sector + 1,
        policing_level=zone.policing_level,
        danger_rating=zone.danger_rating,
        created_at=zone.created_at.isoformat() if zone.created_at else "",
        actual_sector_count=sector_count_query,
        avg_security_level=float(avg_security) if avg_security else None
    )


@router.patch("/players/{player_id}", response_model=dict)
async def update_player(
    player_id: str,
    update_data: dict,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Update player information (admin only)"""
    try:
        # Find the player
        player = db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        # Get the associated user
        user = db.query(User).filter(User.id == player.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Associated user not found")

        # Validate and update User fields
        if 'username' in update_data and update_data['username'] != user.username:
            # Check if username is already taken
            existing_user = db.query(User).filter(
                User.username == update_data['username'],
                User.id != user.id
            ).first()
            if existing_user:
                raise HTTPException(status_code=400, detail="Username already taken")
            user.username = update_data['username']

        if 'email' in update_data and update_data['email'] != user.email:
            # Basic email validation
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, update_data['email']):
                raise HTTPException(status_code=400, detail="Invalid email format")

            # Check if email is already taken
            existing_user = db.query(User).filter(
                User.email == update_data['email'],
                User.id != user.id
            ).first()
            if existing_user:
                raise HTTPException(status_code=400, detail="Email already taken")
            user.email = update_data['email']

        # Validate and update Player fields
        if 'credits' in update_data:
            credits = int(update_data['credits'])
            if credits < 0:
                raise HTTPException(status_code=400, detail="Credits cannot be negative")
            player.credits = credits

        if 'turns' in update_data:
            turns = int(update_data['turns'])
            if turns < 0:
                raise HTTPException(status_code=400, detail="Turns cannot be negative")
            player.turns = turns

        # Handle region and sector location updates (region + sector = complete location)
        if 'current_region_id' in update_data:
            if update_data['current_region_id'] is None or update_data['current_region_id'] == '':
                player.current_region_id = None
            else:
                # Verify region exists
                region = db.query(Region).filter(Region.id == update_data['current_region_id']).first()
                if not region:
                    raise HTTPException(status_code=400, detail="Region not found")
                player.current_region_id = update_data['current_region_id']

        if 'current_sector_id' in update_data and update_data['current_sector_id'] is not None:
            sector_id = int(update_data['current_sector_id'])

            # Validate sector exists within the specified region (if region is set)
            region_id = update_data.get('current_region_id') or player.current_region_id
            if region_id:
                # Check sector exists in the specific region
                sector = db.query(Sector).filter(
                    Sector.sector_id == sector_id,
                    Sector.region_id == region_id
                ).first()
                if not sector:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Sector {sector_id} does not exist in the specified region"
                    )
            else:
                # No region specified, just verify sector exists globally
                sector = db.query(Sector).filter(Sector.sector_id == sector_id).first()
                if not sector:
                    raise HTTPException(status_code=400, detail=f"Sector {sector_id} does not exist")

            player.current_sector_id = sector_id

        if 'status' in update_data:
            status = update_data['status']
            if status not in ['active', 'inactive', 'banned', 'suspended']:
                raise HTTPException(status_code=400, detail="Invalid status value")
            # Map status to is_active
            player.is_active = (status == 'active')

        if 'team_id' in update_data:
            if update_data['team_id'] is None or update_data['team_id'] == '':
                player.team_id = None
            else:
                # Verify team exists
                team = db.query(Team).filter(Team.id == update_data['team_id']).first()
                if not team:
                    raise HTTPException(status_code=400, detail="Team not found")
                player.team_id = update_data['team_id']

        # Commit changes
        db.commit()
        db.refresh(player)
        db.refresh(user)

        # Return updated player data in same format as GET /players
        from src.models.ship import Ship
        ships_count = db.query(Ship).filter(Ship.owner_id == player.id).count()
        planets_count = db.query(Planet).filter(Planet.owner_id == player.id).count()
        ports_count = db.query(Station).filter(Station.owner_id == player.id).count()

        return {
            "id": str(player.id),
            "user_id": str(player.user_id),
            "username": user.username,
            "email": user.email,
            "credits": player.credits,
            "turns": player.turns,
            "current_sector_id": player.current_sector_id,
            "current_region_id": str(player.current_region_id) if player.current_region_id else None,
            "current_ship_id": str(player.current_ship_id) if player.current_ship_id else None,
            "team_id": str(player.team_id) if player.team_id else None,
            "is_active": player.is_active,
            "status": "active" if player.is_active else "inactive",
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "assets": {
                "ships_count": ships_count,
                "planets_count": planets_count,
                "ports_count": ports_count,
                "total_value": player.credits
            },
            "message": "Player updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating player {player_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update player: {str(e)}")

@router.get("/colonies", response_model=dict)
async def get_all_colonies(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all colonies (planets) for admin panel"""
    planets = db.query(Planet).all()
    
    # Map to colonies response format
    colonies_list = []
    for planet in planets:
        # Get owner information if planet is colonized
        owner_name = None
        if planet.owner_id:
            try:
                owner = db.query(Player).filter(Player.id == planet.owner_id).first()
                if owner:
                    owner_name = owner.user.username
            except Exception as e:
                logger.warning(f"Failed to resolve owner name for planet {planet.id}: {e}")
                owner_name = "Unknown"

        colonies_list.append({
            "id": str(planet.id),
            "name": planet.name,
            "sector_id": planet.sector_id,
            "type": planet.type.value if planet.type else "UNKNOWN",
            "status": planet.status.value if planet.status else "UNKNOWN",
            "owner_id": str(planet.owner_id) if planet.owner_id else None,
            "owner_name": owner_name if planet.owner_id else "Uncolonized",
            "population": planet.population,
            "max_population": planet.max_population,
            "habitability_score": planet.habitability_score,
            "resource_richness": planet.resource_richness,
            "morale": planet.morale,
            "defense_level": planet.defense_level,
            "colonized_at": planet.colonized_at.isoformat() if planet.colonized_at else None,
            "fuel_ore": getattr(planet, 'fuel_ore', 0),
            "organics": getattr(planet, 'organics', 0),
            "equipment": getattr(planet, 'equipment', 0),
            "fighters": getattr(planet, 'fighters', 0),
            "factory_level": getattr(planet, 'factory_level', 0),
            "farm_level": getattr(planet, 'farm_level', 0),
            "mine_level": getattr(planet, 'mine_level', 0),
            "research_level": getattr(planet, 'research_level', 0),
            "under_siege": getattr(planet, 'under_siege', False),
            "siege_attacker_id": str(getattr(planet, 'siege_attacker_id', None)) if getattr(planet, 'siege_attacker_id', None) else None
        })
    
    return {"colonies": colonies_list}

@router.get("/teams", response_model=dict)
async def get_all_teams(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all teams for admin panel"""
    try:
        teams = db.query(Team).all()
        
        # Build teams response
        teams_list = []
        for team in teams:
            try:
                # Get team statistics with error handling
                members = db.query(Player).filter(Player.team_id == team.id).all()
                member_count = len(members)
                total_credits = sum(member.credits for member in members)
                
                # Get leader info with error handling
                leader_name = "Unknown"
                try:
                    leader = db.query(Player).join(User).filter(Player.id == team.leader_id).first()
                    if leader and leader.user:
                        leader_name = leader.user.username
                except Exception as e:
                    logger.warning(f"Failed to resolve leader name for team {team.id}: {e}")
                
                teams_list.append({
                    "id": str(team.id),
                    "name": team.name,
                    "leader_id": str(team.leader_id) if team.leader_id else None,
                    "leader_name": leader_name,
                    "member_count": member_count,
                    "total_credits": total_credits,
                    "created_at": team.created_at.isoformat() if team.created_at else None,
                    "is_active": getattr(team, 'is_active', True)
                })
            except Exception as e:
                logger.error(f"Error processing team {team.id}: {e}")
                # Add basic team info even if detailed stats fail
                teams_list.append({
                    "id": str(team.id),
                    "name": team.name,
                    "leader_id": str(team.leader_id) if team.leader_id else None,
                    "leader_name": "Unknown",
                    "member_count": 0,
                    "total_credits": 0,
                    "created_at": team.created_at.isoformat() if team.created_at else None,
                    "is_active": True
                })
        
        return {"teams": teams_list}
        
    except Exception as e:
        logger.error(f"Error getting teams: {e}")
        # Return empty list if query fails
        return {"teams": []}

@router.get("/teams/analytics", response_model=dict)
async def get_teams_analytics(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get team analytics for admin dashboard"""
    try:
        # Get all teams
        teams = db.query(Team).all()
        total_teams = len(teams)
        active_teams = sum(1 for team in teams if getattr(team, 'is_active', True))
        
        # Calculate total members across all teams
        total_members = 0
        most_powerful_team = None
        largest_team = None
        max_combat_rating = 0
        max_member_count = 0
        
        for team in teams:
            try:
                # Get member count for this team
                members = db.query(Player).filter(Player.team_id == team.id).all()
                member_count = len(members)
                total_members += member_count
                
                # Track largest team
                if member_count > max_member_count:
                    max_member_count = member_count
                    largest_team = {
                        "id": str(team.id),
                        "name": team.name,
                        "memberCount": member_count
                    }
                
                # Calculate combat rating (simplified)
                try:
                    total_combat_rating = sum(getattr(member, 'combat_rating', 0) for member in members)
                    if total_combat_rating > max_combat_rating:
                        max_combat_rating = total_combat_rating
                        most_powerful_team = {
                            "id": str(team.id),
                            "name": team.name,
                            "totalCombatRating": total_combat_rating
                        }
                except Exception as e:
                    logger.warning(f"Failed to calculate combat rating for team {team.id}: {e}")

            except Exception as e:
                logger.error(f"Error processing team {team.id}: {e}")
                continue

        # Calculate average team size
        average_team_size = total_members / total_teams if total_teams > 0 else 0

        # Get alliance count (if alliance table exists)
        total_alliances = 0
        try:
            # Try to get alliance count - this might fail if the table doesn't exist
            from src.models.alliance import Alliance
            total_alliances = db.query(Alliance).count()
        except Exception as e:
            logger.warning(f"Failed to query alliance count: {e}")
            total_alliances = 0
        
        return {
            "totalTeams": total_teams,
            "activeTeams": active_teams,
            "totalMembers": total_members,
            "averageTeamSize": round(average_team_size, 1),
            "totalAlliances": total_alliances,
            "mostPowerfulTeam": most_powerful_team,
            "largestTeam": largest_team
        }
        
    except Exception as e:
        logger.error(f"Error getting team analytics: {e}")
        # Return empty analytics if query fails
        return {
            "totalTeams": 0,
            "activeTeams": 0,
            "totalMembers": 0,
            "averageTeamSize": 0,
            "totalAlliances": 0,
            "mostPowerfulTeam": None,
            "largestTeam": None
        }

@router.get("/stats", response_model=dict)
async def get_admin_stats(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get statistics for admin dashboard"""
    try:
        # Ensure we have a clean transaction state
        db.rollback()
        
        # Get real data from database
        total_users = db.query(User).count()
        active_players = db.query(Player).count()
        
        # Get sector count
        total_sectors = db.query(Sector).count()
        
        # Get planet count
        total_planets = db.query(Planet).count()
        
        # Get ship count
        from src.models.ship import Ship
        total_ships = db.query(Ship).count()
        
        # Get warp tunnel count
        total_warp_tunnels = db.query(WarpTunnel).count()
        
        # Get port count
        total_ports = db.query(Station).count()
        
        # For active sessions, we'll count players with recent activity (last 24 hours)
        from datetime import datetime, timedelta, timezone
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
        
        # Simplify the session query to avoid complex logic that might fail
        try:
            active_sessions = db.query(Player).filter(
                Player.last_game_login >= cutoff_time
            ).count()
        except Exception as e:
            logger.warning(f"Failed to query active sessions: {e}")
            active_sessions = 0

        # Get new players today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            new_players_today = db.query(Player).filter(
                Player.created_at >= today_start
            ).count()
        except Exception as e:
            logger.warning(f"Failed to query new players today: {e}")
            new_players_today = 0

        # Get new players this week
        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            new_players_week = db.query(Player).filter(
                Player.created_at >= week_start
            ).count()
        except Exception as e:
            logger.warning(f"Failed to query new players this week: {e}")
            new_players_week = 0
        
        # Ensure we commit the read transactions
        db.commit()
        
        return {
            "total_users": total_users,
            "total_players": active_players,
            "total_sectors": total_sectors,
            "total_planets": total_planets,
            "total_ports": total_ports,
            "total_ships": total_ships,
            "total_warp_tunnels": total_warp_tunnels,
            "active_sessions": active_sessions,
            "new_players_today": new_players_today,
            "new_players_week": new_players_week
        }
        
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        # Ensure transaction is rolled back
        db.rollback()
        
        # Try to get basic stats with individual error handling
        try:
            total_users = db.query(User).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query user count: {e}")
            total_users = 0

        try:
            active_players = db.query(Player).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query player count: {e}")
            active_players = 0

        try:
            total_sectors = db.query(Sector).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query sector count: {e}")
            total_sectors = 0

        try:
            total_planets = db.query(Planet).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query planet count: {e}")
            total_planets = 0

        try:
            from src.models.ship import Ship
            total_ships = db.query(Ship).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query ship count: {e}")
            total_ships = 0

        try:
            total_warp_tunnels = db.query(WarpTunnel).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query warp tunnel count: {e}")
            total_warp_tunnels = 0

        try:
            total_ports = db.query(Station).count()
        except Exception as e:
            logger.warning(f"Fallback: failed to query port count: {e}")
            total_ports = 0
        
        return {
            "total_users": total_users,
            "total_players": active_players,
            "total_sectors": total_sectors,
            "total_planets": total_planets,
            "total_ports": total_ports,
            "total_ships": total_ships,
            "total_warp_tunnels": total_warp_tunnels,
            "active_sessions": 0,
            "new_players_today": 0,
            "new_players_week": 0
        }

@router.get("/galaxy")
async def get_galaxy_info(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get galaxy information for admin panel"""
    # Get the first galaxy (in the future, might support multiple galaxies)
    galaxy = db.query(Galaxy).first()
    
    if not galaxy:
        # Return empty response if no galaxy exists
        return {"galaxy": None}
    
    # Get real statistics with error handling
    try:
        active_players = db.query(Player).count()
    except Exception as e:
        logger.warning(f"Failed to query player count for galaxy info: {e}")
        active_players = 0

    try:
        total_sectors = db.query(Sector).count()
        discovered_sectors = db.query(Sector).filter(Sector.is_discovered == True).count()
    except Exception as e:
        logger.warning(f"Failed to query sector counts for galaxy info: {e}")
        total_sectors = 0
        discovered_sectors = 0

    try:
        station_count = db.query(Station).count()
    except Exception as e:
        logger.warning(f"Failed to query station count for galaxy info: {e}")
        station_count = 0

    try:
        planet_count = db.query(Planet).count()
    except Exception as e:
        logger.warning(f"Failed to query planet count for galaxy info: {e}")
        planet_count = 0

    try:
        team_count = db.query(Team).count()
    except Exception as e:
        logger.warning(f"Failed to query team count for galaxy info: {e}")
        team_count = 0

    # Commit any pending transactions to avoid aborted transaction state
    try:
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to commit pending transactions for galaxy info: {e}")
        db.rollback()

    try:
        warp_tunnel_count = db.query(WarpTunnel).count()
        logger.info(f"Warp tunnel count from ORM query: {warp_tunnel_count}")
    except Exception as e:
        logger.warning(f"Failed to query WarpTunnel model: {e}")
        # Fallback to raw SQL query if SQLAlchemy model fails
        try:
            result = db.execute(text("SELECT COUNT(*) FROM warp_tunnels"))
            warp_tunnel_count = result.scalar() or 0
            logger.info(f"Warp tunnel count from raw SQL: {warp_tunnel_count}")
        except Exception as e2:
            logger.error(f"Failed to query warp_tunnels table: {e2}")
            warp_tunnel_count = 0

    # sector_warps is the in-region adjacency graph players actually use
    # to traverse between sectors. This is what bang generates and what
    # makes a galaxy navigable; warp_tunnels is the smaller set of
    # cross-region gates + player-built / quantum / ancient tunnels.
    try:
        result = db.execute(text("SELECT COUNT(*) FROM sector_warps"))
        sector_warp_count = result.scalar() or 0
    except Exception as e:
        logger.error(f"Failed to query sector_warps table: {e}")
        sector_warp_count = 0

    return {
        "id": str(galaxy.id),
        "name": galaxy.name,
        "created_at": galaxy.created_at.isoformat(),
        "last_updated": galaxy.last_updated.isoformat(),
        # zone_distribution field removed - zones concept eliminated
        "statistics": {
            "total_sectors": total_sectors,
            "discovered_sectors": discovered_sectors,
            "station_count": station_count,
            "planet_count": planet_count,
            "player_count": active_players,
            "team_count": team_count,
            "warp_tunnel_count": warp_tunnel_count,
            "sector_warp_count": sector_warp_count,
            "genesis_count": galaxy.statistics.get("genesis_count", 0)
        },
        "density": galaxy.density,
        "faction_influence": galaxy.faction_influence,
        "state": {
            **galaxy.state,
            "exploration_percentage": (discovered_sectors / total_sectors * 100) if total_sectors > 0 else 0
        },
        "events": galaxy.events,
        # ADR-0006: expansion_enabled / warp_shifts_enabled dropped (galaxy
        # evolves only via region attachment, not in-place mutation).
        "max_sectors": galaxy.max_sectors,
        "resources_regenerate": galaxy.resources_regenerate,
        "default_turns_per_day": galaxy.default_turns_per_day,
        "combat_penalties": galaxy.combat_penalties,
        "economic_modifiers": galaxy.economic_modifiers,
        "hidden_sectors": galaxy.hidden_sectors,
        "special_features": galaxy.special_features,
        "description": galaxy.description,
        # Bang provenance + audit columns (Phase 1B). All optional — pre-bang
        # galaxies will return null for everything in this block.
        "bang": {
            "import_state": (
                galaxy.import_state.value if getattr(galaxy, "import_state", None) is not None
                and hasattr(galaxy.import_state, "value")
                else getattr(galaxy, "import_state", None)
            ),
            "bang_version": getattr(galaxy, "bang_version", None),
            "bang_seed": getattr(galaxy, "bang_seed", None),
            "bang_config_hash": getattr(galaxy, "bang_config_hash", None),
            "generation_warnings": getattr(galaxy, "generation_warnings", None) or [],
            # Snapshot is large; expose lightweight stats only.
            "stats": (
                (galaxy.bang_snapshot or {}).get("stats")
                if getattr(galaxy, "bang_snapshot", None) else None
            ),
        },
        # Legacy support for frontend
        "generation_config": {
            "resource_distribution": galaxy.density.get("resource_distribution", "balanced"),
            "hazard_levels": "moderate",
            "connectivity": "normal",
            "port_density": galaxy.density.get("port_density", 0.15) / 100,
            "planet_density": galaxy.density.get("planet_density", 0.25) / 100,
            "warp_tunnel_probability": galaxy.density.get("one_way_warp_percentage", 0.1) / 100
        }
    }

@router.post("/galaxy/generate", response_model=dict)
async def generate_galaxy(
    request: GalaxyGenerateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
):
    """Deprecated: legacy Python galaxy generator removed in Phase 4 of the
    sw2102-bang cutover. The synchronous, monolithic generator has been replaced
    by the bang sidecar pipeline. Galaxy creation now flows through a job-based
    API that supports preview, commit, live progress, and atomic multi-region
    builds.

    Use ``POST /api/v1/admin/galaxy/jobs`` instead. See
    ``DOCS/PLANS/bang-integration.md`` for the new contract.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "endpoint_removed",
            "message": (
                "POST /api/admin/galaxy/generate was removed in the bang "
                "integration cutover (Phase 4). Use POST /api/v1/admin/galaxy/jobs."
            ),
            "replacement": "/api/v1/admin/galaxy/jobs",
            "docs": "DOCS/PLANS/bang-integration.md",
        },
    )

# Zone endpoints removed - zones concept eliminated
# Architecture: Galaxy → Region → Cluster → Sector
# Use /regions/{region_id}/clusters to get clusters in a region

@router.get("/clusters", response_model=dict)
async def get_all_clusters(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all clusters across all zones"""
    clusters = db.query(Cluster).all()
    
    cluster_list = [
        {
            "id": str(cluster.id),
            "name": cluster.name,
            "type": cluster.type.value,
            "created_at": cluster.created_at.isoformat(),
            "zone_id": str(cluster.zone_id),
            "sector_count": cluster.sector_count,
            "is_discovered": cluster.is_discovered,
            "discovery_requirement": cluster.discovery_requirement,
            "stats": cluster.stats,
            "resources": cluster.resources,
            "economic_value": cluster.economic_value,
            "faction_influence": cluster.faction_influence,
            "nav_hazards": cluster.nav_hazards,
            "recommended_ship_class": cluster.recommended_ship_class,
            "coordinates": {
                "x": cluster.x_coord,
                "y": cluster.y_coord,
                "z": cluster.z_coord
            },
            "is_hidden": cluster.is_hidden,
            "special_features": cluster.special_features,
            "description": cluster.description,
            "warp_stability": cluster.warp_stability,
            # Legacy support for frontend
            "controlling_faction": cluster.controlling_faction
        }
        for cluster in clusters
    ]
    
    return {"clusters": cluster_list}

@router.get("/stations", response_model=dict)
async def get_all_stations(
    limit: int = 100,
    offset: int = 0,
    search: Optional[str] = None,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all stations with pagination"""
    try:
        from src.services.docking_service import docking_fee_for

        query = db.query(Station)
        if search:
            query = query.filter(Station.name.ilike(f"%{search}%"))
        total = query.count()
        stations = query.offset(offset).limit(limit).all()

        stations_list = []
        for station in stations:
            # Get sector info
            sector = db.query(Sector).filter(Sector.sector_id == station.sector_id).first()

            # Resolve owner display name from the Player -> User chain.
            # owner_id may point at a Player whose display name is nickname or
            # the linked user's username. None when truly unowned (UI then
            # renders "Independent").
            owner_name = None
            if station.owner_id is not None:
                owner = db.query(Player).filter(Player.id == station.owner_id).first()
                if owner is not None:
                    owner_name = owner.username  # nickname or user.username

            # Defense data lives in the Station.defenses JSONB; surface the
            # real shield strength / drone count rather than fabricating one.
            defenses = station.defenses or {}

            stations_list.append({
                "id": str(station.id),
                "name": station.name,
                "sector_id": str(station.sector_id),
                "sector_name": sector.name if sector else "Unknown",
                "station_type": station.type.value if station.type else "TRADING",
                "trade_volume": station.trade_volume or 0,
                # No max_capacity column on Station; null renders as em-dash.
                "max_capacity": None,
                # Real per-class docking fee from the canonical fee table.
                "docking_fee": docking_fee_for(station),
                # Real trade tax column (0-1 float); UI presents as percent.
                "tax_rate": station.tax_rate,
                # Real defensive figures from the JSONB; null when absent.
                "security_level": defenses.get("shield_strength"),
                "defense_drones": defenses.get("defense_drones"),
                "owner_id": str(station.owner_id) if station.owner_id else None,
                "owner_name": owner_name,
                "created_at": station.created_at.isoformat() if station.created_at else None,
                "is_operational": station.status.value == "OPERATIONAL" if station.status else True,
                "commodities": list(station.commodities.keys()) if station.commodities else []
            })

        return {"stations": stations_list, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"Error fetching stations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stations: {str(e)}")

@router.get("/sectors", response_model=dict)
async def get_all_sectors(
    filter_region: Optional[str] = None,
    filter_cluster: Optional[str] = None,
    filter_has_port: Optional[bool] = None,
    filter_has_planet: Optional[bool] = None,
    filter_discovered: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 100,
    offset: Optional[int] = None,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get sectors with optional filtering and honest server-side pagination."""
    query = db.query(Sector)

    if filter_cluster:
        query = query.filter(Sector.cluster_id == filter_cluster)
    elif filter_region:
        query = query.join(Cluster).filter(Cluster.region_id == filter_region)

    if filter_discovered is not None:
        query = query.filter(Sector.is_discovered == filter_discovered)

    if search:
        term = f"%{search.strip()}%"
        # Match by sector name; numeric search also matches the sector number.
        conditions = [Sector.name.ilike(term)]
        if search.strip().isdigit():
            conditions.append(Sector.sector_id == int(search.strip()))
        query = query.filter(or_(*conditions))

    # has_port / has_planet require per-row presence checks; apply them after
    # fetching the page candidates so the count stays a single query for the
    # column-level filters. When those filters are requested we restrict the
    # base query via subquery to keep totals honest.
    if filter_has_port is not None:
        port_subq = db.query(Station.sector_id).distinct().subquery()
        port_sectors = db.query(port_subq.c.sector_id)
        if filter_has_port:
            query = query.filter(Sector.sector_id.in_(port_sectors))
        else:
            query = query.filter(~Sector.sector_id.in_(port_sectors))

    if filter_has_planet is not None:
        planet_subq = db.query(Planet.sector_id).distinct().subquery()
        planet_sectors = db.query(planet_subq.c.sector_id)
        if filter_has_planet:
            query = query.filter(Sector.sector_id.in_(planet_sectors))
        else:
            query = query.filter(~Sector.sector_id.in_(planet_sectors))

    total = query.count()

    # offset wins if explicitly supplied; otherwise derive from 1-based page.
    effective_offset = offset if offset is not None else max(page - 1, 0) * limit
    sectors = query.offset(effective_offset).limit(limit).all()

    # Resolve cluster -> region display names for the page in two batched
    # lookups rather than per-row joins.
    cluster_ids = {sector.cluster_id for sector in sectors if sector.cluster_id is not None}
    cluster_map = {}
    region_map = {}
    if cluster_ids:
        clusters = db.query(Cluster).filter(Cluster.id.in_(cluster_ids)).all()
        cluster_map = {c.id: c for c in clusters}
        region_ids = {c.region_id for c in clusters if c.region_id is not None}
        if region_ids:
            regions = db.query(Region).filter(Region.id.in_(region_ids)).all()
            region_map = {r.id: r for r in regions}

    sector_list = []
    for sector in sectors:
        # Check for port in this sector
        has_port = db.query(Station).filter(Station.sector_id == sector.sector_id).first() is not None

        # Check for planet in this sector
        has_planet = db.query(Planet).filter(Planet.sector_id == sector.sector_id).first() is not None

        # Check for warp tunnels from this sector (using UUID sector.id, not integer sector_id)
        has_warp_tunnel = db.query(WarpTunnel).filter(
            (WarpTunnel.origin_sector_id == sector.id) |
            (WarpTunnel.destination_sector_id == sector.id)
        ).first() is not None

        # Resolve region/cluster names from the batched lookups.
        cluster = cluster_map.get(sector.cluster_id)
        cluster_name = cluster.name if cluster else None
        region = region_map.get(cluster.region_id) if cluster else None
        region_name = region.display_name if region else None

        sector_list.append({
            "id": str(sector.id),
            "sector_id": sector.sector_id,
            "name": sector.name,
            "type": sector.special_type.value if hasattr(sector, 'special_type') and sector.special_type is not None else sector.type.value,
            "cluster_id": str(sector.cluster_id),
            "cluster_name": cluster_name,
            "region_name": region_name,
            "x_coord": sector.x_coord,
            "y_coord": sector.y_coord,
            "z_coord": sector.z_coord,
            "hazard_level": sector.hazard_level,
            "is_discovered": sector.is_discovered,
            "is_navigable": True,  # Default to True, override if nav_hazards exist
            "has_port": has_port,
            "has_planet": has_planet,
            "has_warp_tunnel": has_warp_tunnel,
            # Real richness derived from the resources JSONB: rich when this
            # sector has scanned asteroid yields, otherwise null (no canonical
            # richness scalar exists on the model — do not fabricate one).
            "resource_richness": _sector_resource_richness(sector.resources),
            "controlling_faction": sector.controlling_faction
        })

    return {
        "sectors": sector_list,
        "total": total,
        "total_count": total,
        "limit": limit,
        "offset": effective_offset,
        "page": page,
    }


def _sector_resource_richness(resources):
    """Derive an honest richness label from the Sector.resources JSONB.

    The model has no richness scalar; we report based on real asteroid yield
    data. Returns None when the sector has not been scanned / has no asteroid
    data, so the UI can render an em-dash rather than an invented value.
    """
    if not resources or not resources.get("has_asteroids"):
        return None
    yields = resources.get("asteroid_yield") or {}
    total_yield = sum(v for v in yields.values() if isinstance(v, (int, float)))
    if total_yield <= 0:
        return None
    if total_yield >= 5000:
        return "rich"
    if total_yield >= 1000:
        return "moderate"
    return "sparse"

@router.post("/warp-tunnels/create", response_model=dict)
async def create_warp_tunnel(
    request: WarpTunnelCreateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Create a warp tunnel between two sectors"""
    try:
        # Find source and target sectors
        source_sector = db.query(Sector).filter(Sector.sector_id == request.source_sector_id).first()
        target_sector = db.query(Sector).filter(Sector.sector_id == request.target_sector_id).first()
        
        if not source_sector or not target_sector:
            raise HTTPException(status_code=404, detail="One or both sectors not found")
        
        # Check if tunnel already exists
        existing_tunnel = db.query(WarpTunnel).filter(
            ((WarpTunnel.origin_sector_id == source_sector.id) & 
             (WarpTunnel.destination_sector_id == target_sector.id)) |
            ((WarpTunnel.origin_sector_id == target_sector.id) & 
             (WarpTunnel.destination_sector_id == source_sector.id))
        ).first()
        
        if existing_tunnel:
            raise HTTPException(status_code=400, detail="Warp tunnel already exists between these sectors")
        
        # Create new warp tunnel
        warp_tunnel = WarpTunnel(
            origin_sector_id=source_sector.id,
            destination_sector_id=target_sector.id,
            stability=request.stability,
            is_bidirectional=True
        )
        
        db.add(warp_tunnel)
        db.commit()
        db.refresh(warp_tunnel)
        
        return {
            "id": str(warp_tunnel.id),
            "source_sector_id": request.source_sector_id,
            "target_sector_id": request.target_sector_id,
            "stability": warp_tunnel.stability,
            "message": "Warp tunnel created successfully"
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create warp tunnel: {str(e)}")

@router.delete("/galaxy/clear", response_model=dict)
async def clear_all_galaxy_data(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Clear all galaxy data for testing purposes (complete wipe including player game state)"""
    try:
        # Delete all universe data in correct order to avoid foreign key constraints
        # Must delete children before parents to avoid FK violations
        # NOTE: Preserves User and OAuthAccount tables (authentication identity)
        # but deletes all game state (Players, Ships, galaxy structure)

        from src.models.npc_character import NPCCharacter
        db.query(NPCCharacter).delete()  # NPC pilots (incl. KIA tombstones) reference Ships; must not outlive their galaxy
        db.query(Ship).delete()          # Ships reference Players + Sectors
        db.query(Player).delete()        # Players reference Sectors + Regions + Ships (via current_ship_id)
        db.query(Station).delete()       # Stations reference Sectors
        db.query(Planet).delete()        # Planets reference Sectors
        db.query(WarpTunnel).delete()    # Warp tunnels reference Sectors
        db.query(Sector).delete()        # Sectors reference Clusters AND Regions
        db.query(Cluster).delete()       # Clusters reference Regions
        db.query(Region).delete()        # Regions (includes Central Nexus), referenced by Sectors
        db.query(Galaxy).delete()        # Finally delete Galaxy
        db.commit()

        return {"message": "All galaxy data and player game state cleared successfully. User accounts preserved."}

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to clear galaxy data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to clear galaxy data: {str(e)}")

@router.post("/galaxy/fix-statistics", response_model=dict)
async def fix_galaxy_statistics(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Migrate galaxy statistics from old field names (port_count) to new field names (station_count)"""
    try:
        from sqlalchemy.orm.attributes import flag_modified

        galaxy = db.query(Galaxy).first()
        if not galaxy:
            raise HTTPException(status_code=404, detail="No galaxy found")

        # Count actual stations in database
        actual_station_count = db.query(Station).count()

        logger.info(f"Found galaxy: {galaxy.name}")
        logger.info(f"Current statistics: {galaxy.statistics}")
        logger.info(f"Actual stations in database: {actual_station_count}")

        # Migrate port_count -> station_count
        if 'port_count' in galaxy.statistics:
            galaxy.statistics['station_count'] = galaxy.statistics['port_count']
            del galaxy.statistics['port_count']
            logger.info("Renamed port_count -> station_count")
        else:
            # Set station_count to actual count if it doesn't exist
            galaxy.statistics['station_count'] = actual_station_count
            logger.info("Added station_count field")

        # Migrate port_density -> station_density
        if 'port_density' in galaxy.statistics:
            galaxy.statistics['station_density'] = galaxy.statistics['port_density']
            del galaxy.statistics['port_density']
            logger.info("Renamed port_density -> station_density")

        # Mark as modified so SQLAlchemy knows to update the JSON field
        flag_modified(galaxy, 'statistics')
        db.commit()

        logger.info(f"Updated statistics: {galaxy.statistics}")

        return {
            "message": "Galaxy statistics fixed successfully",
            "updated_statistics": galaxy.statistics,
            "actual_station_count": actual_station_count
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to fix galaxy statistics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fix galaxy statistics: {str(e)}")

# NOTE: DELETE /galaxy/{galaxy_id} intentionally removed from this router.
# It shadowed bang_galaxy.py's proper cascade hard-delete (same path, mounted
# later in api.py), which requires the X-Confirm-Galaxy-Name header and
# cascades all galaxy contents. bang_galaxy.py now answers that route.

@router.get("/sectors/{sector_id}/port", response_model=dict)
async def get_sector_port(
    sector_id: int,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get port details for a specific sector"""
    station = db.query(Station).filter(Station.sector_id == sector_id).first()

    if not station:
        return {
            "has_port": False,
            "station": None
        }

    # Extract defense data from JSONB field
    defenses = station.defenses or {}

    return {
        "has_port": True,
        "station": {
            "id": str(station.id),
            "name": station.name,
            "sector_id": station.sector_id,
            "station_class": station.station_class.value if station.station_class else None,
            "type": station.type.value if station.type else None,
            "status": station.status.value if station.status else None,
            "size": station.size,
            "owner_id": str(station.owner_id) if station.owner_id else None,
            "faction_affiliation": station.faction_affiliation,
            "trade_volume": station.trade_volume,
            "market_volatility": station.market_volatility,
            "tax_rate": station.tax_rate,  # Real trade tax column (0-1 float)

            # Defense information from JSONB
            "defense_level": defenses.get("defense_drones", 0),
            "defense_drones": defenses.get("defense_drones", 0),
            "max_defense_drones": defenses.get("max_defense_drones", 50),
            "shields": defenses.get("shield_strength", 50),
            "defense_weapons": defenses.get("defense_drones", 0),  # Using defense_drones as weapons count
            "patrol_ships": defenses.get("patrol_ships", 0),

            # Services and pricing
            "services": station.services,
            "service_prices": station.service_prices,
            "price_modifiers": station.price_modifiers,
            "commodities": station.commodities,

            # Management
            "ownership": station.ownership,
            "is_player_ownable": station.is_player_ownable,
            "reputation_threshold": station.reputation_threshold,

            # Market information
            "last_market_update": station.last_market_update.isoformat() if station.last_market_update else None,
            "market_update_frequency": station.market_update_frequency,

            # Special flags
            "is_quest_hub": station.is_quest_hub,
            "is_faction_headquarters": station.is_faction_headquarters,

            # Acquisition requirements
            "acquisition_requirements": station.acquisition_requirements
        }
    }

@router.get("/sectors/{sector_id}/planet", response_model=dict)
async def get_sector_planet(
    sector_id: int,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get planet details for a specific sector"""
    planet = db.query(Planet).filter(Planet.sector_id == sector_id).first()

    if not planet:
        return {
            "has_planet": False,
            "planet": None
        }

    return {
        "has_planet": True,
        "planet": {
            "id": str(planet.id),
            "name": planet.name,
            "sector_id": planet.sector_id,
            "type": planet.type.value if planet.type else None,
            "status": planet.status.value if planet.status else None,
            "size": planet.size,
            "position": planet.position,
            "gravity": planet.gravity,
            "atmosphere": planet.atmosphere,
            "temperature": planet.temperature,
            "water_coverage": planet.water_coverage,
            "habitability_score": planet.habitability_score,
            "radiation_level": planet.radiation_level,
            "resource_richness": planet.resource_richness,
            "resources": planet.resources,
            "special_resources": planet.special_resources,
            "owner_id": str(planet.owner_id) if planet.owner_id else None,
            "colonized_at": planet.colonized_at.isoformat() if planet.colonized_at else None,
            "population": planet.population,
            "max_population": planet.max_population,
            "population_growth": planet.population_growth,
            "economy": planet.economy,
            "production": planet.production,
            "production_efficiency": planet.production_efficiency,
            "defense_level": planet.defense_level,
            "shields": planet.shields,
            "weapon_batteries": planet.weapon_batteries,
            "last_attacked": planet.last_attacked.isoformat() if planet.last_attacked else None,
            "last_production": planet.last_production.isoformat() if planet.last_production else None,
            "active_events": planet.active_events,
            "description": planet.description,
            "genesis_created": planet.genesis_created,
            "genesis_device_id": str(planet.genesis_device_id) if planet.genesis_device_id else None
        }
    }

@router.get("/sectors/{sector_id}/ships", response_model=dict)
async def get_sector_ships(
    sector_id: int,
    _: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all ships currently in a specific sector"""
    from src.models.ship import Ship
    
    ships = db.query(Ship).filter(Ship.sector_id == sector_id).all()
    
    ship_list = [
        {
            "id": str(ship.id),
            "name": ship.name,
            "type": ship.type.value,
            "owner_id": str(ship.owner_id),
            "owner_name": ship.owner.username if ship.owner else "Unknown"
        }
        for ship in ships
    ]
    
    return {"ships": ship_list}

@router.get("/alliances", response_model=dict)
async def get_all_alliances(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all alliances for admin panel"""
    try:
        # Try to get alliances - this might fail if the table doesn't exist
        try:
            from src.models.alliance import Alliance
            alliances = db.query(Alliance).all()
            
            alliances_list = []
            for alliance in alliances:
                try:
                    alliances_list.append({
                        "id": str(alliance.id),
                        "name": getattr(alliance, 'name', f"Alliance {alliance.id}"),
                        "type": getattr(alliance, 'type', 'unknown'),
                        "team1Id": str(getattr(alliance, 'team1_id', '')),
                        "team2Id": str(getattr(alliance, 'team2_id', '')),
                        "status": getattr(alliance, 'status', 'active'),
                        "created_at": alliance.created_at.isoformat() if hasattr(alliance, 'created_at') and alliance.created_at else None
                    })
                except Exception as e:
                    logger.error(f"Error processing alliance {alliance.id}: {e}")
                    continue
            
            return {"alliances": alliances_list}
            
        except Exception as e:
            logger.warning(f"Alliance table not available: {e}")
            return {"alliances": []}
            
    except Exception as e:
        logger.error(f"Error getting alliances: {e}")
        return {"alliances": []}

@router.patch("/ports/{station_id}", response_model=dict)
async def update_port(
    station_id: str,
    port_updates: dict,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Update port details including commodity quantities"""
    try:
        station = db.query(Station).filter(Station.id == station_id).first()

        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        
        # Handle commodity updates
        if 'commodities' in port_updates:
            # Update specific commodity fields
            for commodity_name, updates in port_updates['commodities'].items():
                if commodity_name in station.commodities:
                    for field, value in updates.items():
                        station.commodities[commodity_name][field] = value
        
        # Handle direct field updates (like quantity updates from frontend)
        for field, value in port_updates.items():
            if field == 'commodities':
                continue  # Already handled above
            elif hasattr(station, field):
                setattr(station, field, value)
            elif field.endswith('_quantity'):
                # Handle direct quantity updates like "ore_quantity"
                commodity_name = field.replace('_quantity', '')
                if commodity_name in station.commodities:
                    station.commodities[commodity_name]['quantity'] = value
        
        # Mark commodities as modified for SQLAlchemy
        station.commodities = dict(station.commodities)
        
        db.commit()
        
        return {
            "success": True,
            "message": "Station updated successfully",
            "station_id": str(station.id)
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating port: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update port: {str(e)}")


# ============================================================================
# Game Event Management Endpoints
# ============================================================================
# These provide dashboard-level event management alongside the more
# comprehensive CRUD in /admin/events (events.py). These endpoints are
# designed for the admin dashboard summary views and quick-action workflows.
# ============================================================================

@router.get("/game-events/summary", response_model=dict)
async def get_game_events_summary(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get a summary of game events for the admin dashboard.

    Returns counts by status, the most recent events, and currently
    active effects so the dashboard can render an at-a-glance widget.
    """
    try:
        total_events = db.query(GameEvent).count()
        active_events = db.query(GameEvent).filter(
            GameEvent.status == EventStatus.ACTIVE
        ).count()
        scheduled_events = db.query(GameEvent).filter(
            GameEvent.status == EventStatus.SCHEDULED
        ).count()
        completed_events = db.query(GameEvent).filter(
            GameEvent.status == EventStatus.COMPLETED
        ).count()
        cancelled_events = db.query(GameEvent).filter(
            GameEvent.status == EventStatus.CANCELLED
        ).count()

        # Total participation across all events
        total_participants = db.query(EventParticipation).count()

        # Total rewards distributed
        rewards_result = db.query(func.sum(GameEvent.rewards_distributed)).scalar()
        total_rewards = int(rewards_result) if rewards_result else 0

        # Fetch the 5 most recent events (any status)
        recent_events = (
            db.query(GameEvent)
            .order_by(desc(GameEvent.created_at))
            .limit(5)
            .all()
        )

        recent_list = []
        for event in recent_events:
            creator = db.query(User).filter(User.id == event.created_by).first()
            recent_list.append({
                "id": str(event.id),
                "title": event.title,
                "event_type": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
                "status": event.status.value if isinstance(event.status, EventStatus) else str(event.status),
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "created_by": creator.username if creator else "System",
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "participation_count": event.participation_count or 0,
            })

        # Currently active effects
        active_effects = (
            db.query(EventEffect)
            .filter(EventEffect.is_active == True)
            .all()
        )
        effects_list = [
            {
                "id": str(eff.id),
                "event_id": str(eff.event_id),
                "effect_type": eff.effect_type,
                "target": eff.target,
                "modifier": eff.modifier,
                "description": eff.description,
                "applied_at": eff.applied_at.isoformat() if eff.applied_at else None,
                "expires_at": eff.expires_at.isoformat() if eff.expires_at else None,
            }
            for eff in active_effects
        ]

        return {
            "counts": {
                "total": total_events,
                "active": active_events,
                "scheduled": scheduled_events,
                "completed": completed_events,
                "cancelled": cancelled_events,
            },
            "total_participants": total_participants,
            "total_rewards_distributed": total_rewards,
            "recent_events": recent_list,
            "active_effects": effects_list,
        }

    except Exception as e:
        logger.error(f"Error fetching game events summary: {e}")
        return {
            "counts": {"total": 0, "active": 0, "scheduled": 0, "completed": 0, "cancelled": 0},
            "total_participants": 0,
            "total_rewards_distributed": 0,
            "recent_events": [],
            "active_effects": [],
        }


@router.get("/game-events", response_model=dict)
async def list_game_events(
    status_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """List game events with optional filters.

    Provides a simpler list view than the comprehensive paginated endpoint
    in events.py, suitable for admin dashboard tables.
    """
    try:
        query = db.query(GameEvent)

        if status_filter and status_filter != "all":
            try:
                query = query.filter(GameEvent.status == EventStatus(status_filter))
            except ValueError:
                pass  # ignore invalid filter

        if type_filter and type_filter != "all":
            try:
                query = query.filter(GameEvent.event_type == EventType(type_filter))
            except ValueError:
                pass

        total = query.count()
        events = query.order_by(desc(GameEvent.created_at)).offset(offset).limit(limit).all()

        events_list = []
        for event in events:
            # Count effects
            effect_count = db.query(EventEffect).filter(EventEffect.event_id == event.id).count()

            # Get creator name
            creator = db.query(User).filter(User.id == event.created_by).first()

            events_list.append({
                "id": str(event.id),
                "title": event.title,
                "description": event.description,
                "event_type": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
                "status": event.status.value if isinstance(event.status, EventStatus) else str(event.status),
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "actual_start_time": event.actual_start_time.isoformat() if event.actual_start_time else None,
                "actual_end_time": event.actual_end_time.isoformat() if event.actual_end_time else None,
                "affected_regions": event.affected_regions or [],
                "global_event": event.global_event,
                "effect_count": effect_count,
                "participation_count": event.participation_count or 0,
                "rewards_distributed": event.rewards_distributed or 0,
                "auto_start": event.auto_start,
                "priority": event.priority,
                "created_by": creator.username if creator else "System",
                "created_at": event.created_at.isoformat() if event.created_at else None,
            })

        return {"events": events_list, "total": total}

    except Exception as e:
        logger.error(f"Error listing game events: {e}")
        return {"events": [], "total": 0}


@router.post("/game-events", response_model=dict)
async def create_game_event(
    event_data: QuickEventCreateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Create a new game event with a simplified payload.

    This is a quick-create pathway for the admin dashboard. For full
    control (participation requirements, rewards config, etc.) use the
    comprehensive POST /admin/events/ endpoint.
    """
    try:
        # Validate event type
        try:
            event_type = EventType(event_data.event_type)
        except ValueError:
            valid_types = [t.value for t in EventType]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event_type '{event_data.event_type}'. Valid types: {valid_types}"
            )

        now = datetime.now(timezone.utc)
        from datetime import timedelta
        end_time = now + timedelta(hours=event_data.duration_hours)

        new_event = GameEvent(
            title=event_data.title,
            description=event_data.description,
            event_type=event_type,
            status=EventStatus.ACTIVE if event_data.auto_start else EventStatus.SCHEDULED,
            start_time=now if event_data.auto_start else now,
            end_time=end_time,
            actual_start_time=now if event_data.auto_start else None,
            affected_regions=event_data.affected_regions,
            global_event=(event_data.affected_regions is None or len(event_data.affected_regions) == 0),
            auto_start=event_data.auto_start,
            created_by=current_admin.id,
            created_at=now,
        )

        db.add(new_event)
        db.flush()  # get the id

        # Create effects if provided
        effects_created = 0
        if event_data.effects:
            for eff_data in event_data.effects:
                effect = EventEffect(
                    event_id=new_event.id,
                    effect_type=eff_data.get("type", "modifier"),
                    target=eff_data.get("target", "global"),
                    modifier=float(eff_data.get("modifier", 1.0)),
                    duration_hours=eff_data.get("duration_hours", event_data.duration_hours),
                    description=eff_data.get("description", ""),
                    is_active=event_data.auto_start,
                    applied_at=now if event_data.auto_start else None,
                )
                db.add(effect)
                effects_created += 1

        db.commit()
        db.refresh(new_event)

        return {
            "success": True,
            "event": {
                "id": str(new_event.id),
                "title": new_event.title,
                "description": new_event.description,
                "event_type": new_event.event_type.value,
                "status": new_event.status.value,
                "start_time": new_event.start_time.isoformat(),
                "end_time": new_event.end_time.isoformat() if new_event.end_time else None,
                "affected_regions": new_event.affected_regions or [],
                "global_event": new_event.global_event,
                "effects_created": effects_created,
                "created_by": current_admin.username,
                "created_at": new_event.created_at.isoformat(),
            },
            "message": f"Event '{new_event.title}' created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating game event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create game event: {str(e)}")


@router.get("/game-events/active/current", response_model=dict)
async def get_active_game_events(
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all currently active game events with their effects.

    This endpoint is designed for the admin dashboard to show what events
    are actively affecting the game world right now.
    """
    try:
        active_events = (
            db.query(GameEvent)
            .filter(GameEvent.status == EventStatus.ACTIVE)
            .order_by(desc(GameEvent.actual_start_time))
            .all()
        )

        events_list = []
        for event in active_events:
            effects = db.query(EventEffect).filter(
                EventEffect.event_id == event.id,
                EventEffect.is_active == True
            ).all()

            effects_list = [
                {
                    "effect_type": eff.effect_type,
                    "target": eff.target,
                    "modifier": eff.modifier,
                    "description": eff.description,
                }
                for eff in effects
            ]

            events_list.append({
                "id": str(event.id),
                "title": event.title,
                "description": event.description,
                "event_type": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "actual_start_time": event.actual_start_time.isoformat() if event.actual_start_time else None,
                "affected_regions": event.affected_regions or [],
                "global_event": event.global_event,
                "effects": effects_list,
                "participation_count": event.participation_count or 0,
                "priority": event.priority,
            })

        return {"active_events": events_list, "total": len(events_list)}

    except Exception as e:
        logger.error(f"Error fetching active game events: {e}")
        return {"active_events": [], "total": 0}


@router.get("/game-events/{event_id}", response_model=dict)
async def get_game_event_detail(
    event_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific game event."""
    try:
        event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        # Get effects
        effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
        effects_list = [
            {
                "id": str(eff.id),
                "effect_type": eff.effect_type,
                "target": eff.target,
                "modifier": eff.modifier,
                "duration_hours": eff.duration_hours,
                "description": eff.description,
                "is_active": eff.is_active,
                "applied_at": eff.applied_at.isoformat() if eff.applied_at else None,
                "expires_at": eff.expires_at.isoformat() if eff.expires_at else None,
            }
            for eff in effects
        ]

        # Get participation count
        participation_count = db.query(EventParticipation).filter(
            EventParticipation.event_id == event.id
        ).count()

        # Get creator
        creator = db.query(User).filter(User.id == event.created_by).first()

        # Get approver if applicable
        approver_name = None
        if event.approved_by:
            approver = db.query(User).filter(User.id == event.approved_by).first()
            approver_name = approver.username if approver else None

        return {
            "event": {
                "id": str(event.id),
                "title": event.title,
                "description": event.description,
                "event_type": event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type),
                "status": event.status.value if isinstance(event.status, EventStatus) else str(event.status),
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "actual_start_time": event.actual_start_time.isoformat() if event.actual_start_time else None,
                "actual_end_time": event.actual_end_time.isoformat() if event.actual_end_time else None,
                "affected_regions": event.affected_regions or [],
                "affected_sectors": event.affected_sectors or [],
                "global_event": event.global_event,
                "auto_start": event.auto_start,
                "auto_end": event.auto_end,
                "repeatable": event.repeatable,
                "priority": event.priority,
                "participation_count": participation_count,
                "max_participants": event.max_participants,
                "rewards_distributed": event.rewards_distributed or 0,
                "completion_rate": event.completion_rate or 0.0,
                "requires_approval": event.requires_approval,
                "approved_by": approver_name,
                "approved_at": event.approved_at.isoformat() if event.approved_at else None,
                "admin_notes": event.admin_notes,
                "effects": effects_list,
                "created_by": creator.username if creator else "System",
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching game event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch game event: {str(e)}")


@router.patch("/game-events/{event_id}", response_model=dict)
async def update_game_event(
    event_id: str,
    update_data: EventUpdateRequest,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Update a game event's basic fields (title, description, status, end_time)."""
    try:
        event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        if update_data.title is not None:
            event.title = update_data.title
        if update_data.description is not None:
            event.description = update_data.description
        if update_data.end_time is not None:
            event.end_time = update_data.end_time

        # Handle status transitions
        if update_data.status is not None:
            try:
                new_status = EventStatus(update_data.status)
            except ValueError:
                valid_statuses = [s.value for s in EventStatus]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status '{update_data.status}'. Valid statuses: {valid_statuses}"
                )

            old_status = event.status
            now = datetime.now(timezone.utc)

            # Enforce valid transitions
            if new_status == EventStatus.ACTIVE and old_status == EventStatus.SCHEDULED:
                event.status = EventStatus.ACTIVE
                event.actual_start_time = now
                # Activate effects
                effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
                for eff in effects:
                    eff.is_active = True
                    eff.applied_at = now

            elif new_status in (EventStatus.COMPLETED, EventStatus.CANCELLED) and old_status in (EventStatus.ACTIVE, EventStatus.SCHEDULED):
                event.status = new_status
                event.actual_end_time = now
                # Deactivate effects
                effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
                for eff in effects:
                    eff.is_active = False

            elif new_status == EventStatus.PAUSED and old_status == EventStatus.ACTIVE:
                event.status = EventStatus.PAUSED
                # Deactivate effects while paused
                effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
                for eff in effects:
                    eff.is_active = False

            elif new_status == EventStatus.ACTIVE and old_status == EventStatus.PAUSED:
                event.status = EventStatus.ACTIVE
                # Reactivate effects
                effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
                for eff in effects:
                    eff.is_active = True
                    eff.applied_at = now

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot transition from '{old_status.value}' to '{new_status.value}'"
                )

        db.commit()
        db.refresh(event)

        return {
            "success": True,
            "event": {
                "id": str(event.id),
                "title": event.title,
                "status": event.status.value if isinstance(event.status, EventStatus) else str(event.status),
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            },
            "message": f"Event '{event.title}' updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating game event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update game event: {str(e)}")


@router.post("/game-events/{event_id}/activate", response_model=dict)
async def activate_game_event(
    event_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Activate a scheduled or paused game event."""
    try:
        event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        if event.status not in (EventStatus.SCHEDULED, EventStatus.PAUSED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot activate event with status '{event.status.value}'. Must be 'scheduled' or 'paused'."
            )

        now = datetime.now(timezone.utc)
        event.status = EventStatus.ACTIVE
        event.actual_start_time = event.actual_start_time or now

        # Activate all effects
        effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
        for eff in effects:
            eff.is_active = True
            eff.applied_at = now

        db.commit()

        return {
            "success": True,
            "event_id": str(event.id),
            "status": "active",
            "message": f"Event '{event.title}' is now active"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error activating game event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to activate game event: {str(e)}")


@router.post("/game-events/{event_id}/deactivate", response_model=dict)
async def deactivate_game_event(
    event_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Deactivate (complete or cancel) an active or scheduled game event."""
    try:
        event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        if event.status not in (EventStatus.ACTIVE, EventStatus.SCHEDULED, EventStatus.PAUSED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deactivate event with status '{event.status.value}'"
            )

        now = datetime.now(timezone.utc)
        # Scheduled events get cancelled; active/paused events get completed
        if event.status == EventStatus.SCHEDULED:
            event.status = EventStatus.CANCELLED
        else:
            event.status = EventStatus.COMPLETED
        event.actual_end_time = now

        # Deactivate all effects
        effects = db.query(EventEffect).filter(EventEffect.event_id == event.id).all()
        for eff in effects:
            eff.is_active = False

        db.commit()

        return {
            "success": True,
            "event_id": str(event.id),
            "status": event.status.value,
            "message": f"Event '{event.title}' has been {event.status.value}"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deactivating game event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to deactivate game event: {str(e)}")


@router.delete("/game-events/{event_id}", response_model=dict)
async def delete_game_event(
    event_id: str,
    current_admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Delete a game event. Active events must be deactivated first."""
    try:
        event = db.query(GameEvent).filter(GameEvent.id == event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        if event.status == EventStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete an active event. Deactivate it first."
            )

        event_title = event.title

        # Delete associated effects and participations (cascade should handle this,
        # but be explicit for safety)
        db.query(EventEffect).filter(EventEffect.event_id == event.id).delete()
        db.query(EventParticipation).filter(EventParticipation.event_id == event.id).delete()
        db.delete(event)
        db.commit()

        return {
            "success": True,
            "event_id": event_id,
            "message": f"Event '{event_title}' deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting game event {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete game event: {str(e)}")


