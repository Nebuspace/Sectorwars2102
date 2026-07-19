"""Debug endpoints for troubleshooting authentication and player issues.
All debug endpoints require admin authentication."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from src.core.database import get_db
from src.auth.admin_scopes import AUDIT_VIEW
from src.auth.dependencies import get_current_user, get_current_player, require_scope
from src.models.user import User
from src.models.player import Player
from src.models.ship import Ship
from src.models.sector import Sector

router = APIRouter()


@router.get("/debug/user-state")
async def get_user_state(
    current_user: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """Get complete user and player state for debugging. Requires admin authentication."""
    
    # Get player record
    player = db.query(Player).filter(Player.user_id == current_user.id).first()
    
    if not player:
        return {
            "user": {
                "id": str(current_user.id),
                "username": current_user.username,
                "email": current_user.email,
                "is_active": current_user.is_active,
                "is_admin": current_user.is_admin,
                "last_login": current_user.last_login
            },
            "player": None,
            "error": "No player record found for this user"
        }
    
    # Get current ship
    current_ship = None
    if player.current_ship_id:
        ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
        if ship:
            current_ship = {
                "id": str(ship.id),
                "name": ship.name,
                "type": ship.type.value if ship.type else None,
                "sector_id": str(ship.sector_id) if ship.sector_id else None
            }
    
    # Get current sector
    current_sector = None
    if player.current_sector_id:
        sector = db.query(Sector).filter(Sector.id == player.current_sector_id).first()
        if sector:
            current_sector = {
                "id": str(sector.id),
                "name": sector.name,
                "region_id": str(sector.region_id) if sector.region_id else None
            }
    
    # Get all ships
    ships = db.query(Ship).filter(Ship.owner_id == player.id).all()
    
    return {
        "user": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "is_active": current_user.is_active,
            "is_admin": current_user.is_admin,
            "last_login": current_user.last_login
        },
        "player": {
            "id": str(player.id),
            "nickname": player.nickname,
            "credits": player.credits,
            "turns": player.turns,
            "current_sector_id": str(player.current_sector_id) if player.current_sector_id else None,
            "home_sector_id": str(player.home_sector_id) if player.home_sector_id else None,
            "current_ship_id": str(player.current_ship_id) if player.current_ship_id else None,
            "ship_count": len(ships),
            "created_at": player.created_at,
            "updated_at": player.updated_at
        },
        "current_ship": current_ship,
        "current_sector": current_sector,
        "ships": [
            {
                "id": str(ship.id),
                "name": ship.name,
                "type": ship.type.value if ship.type else None,
                "sector_id": str(ship.sector_id) if ship.sector_id else None,
                "is_flagship": ship.is_flagship
            }
            for ship in ships
        ]
    }


@router.get("/debug/sector-check")
async def check_sectors(
    current_admin: User = Depends(require_scope(AUDIT_VIEW)),
    db: Session = Depends(get_db)
):
    """Check if sectors exist in the database. Requires admin authentication."""
    sectors = db.query(Sector).limit(5).all()
    
    return {
        "sector_count": db.query(Sector).count(),
        "sample_sectors": [
            {
                "id": str(sector.id),
                "name": sector.name,
                "region_id": str(sector.region_id) if sector.region_id else None
            }
            for sector in sectors
        ]
    }