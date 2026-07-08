import json
import asyncio
import time
from collections import defaultdict
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, Dict
import logging

from pydantic import BaseModel, Field as PydanticField
from src.core.database import get_db
from src.auth.dependencies import get_current_user_from_token, get_current_admin_user
from src.models.user import User
from src.models.player import Player
from src.services.websocket_service import connection_manager, handle_websocket_message, handle_admin_websocket_message


class BroadcastRequest(BaseModel):
    content: str = PydanticField(..., max_length=5000, description="Broadcast message content")
    priority: str = PydanticField(default="normal", description="Message priority")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])

# Per-connection rate limiter: max 100 messages per second
_ws_rate_limits: Dict[str, list] = defaultdict(list)
WS_RATE_LIMIT = 100  # messages per window
WS_RATE_WINDOW = 1.0  # seconds


def _check_ws_rate_limit(user_id: str) -> bool:
    """Return True if under rate limit, False if exceeded."""
    now = time.monotonic()
    timestamps = _ws_rate_limits[user_id]
    # Purge old entries
    _ws_rate_limits[user_id] = [t for t in timestamps if now - t < WS_RATE_WINDOW]
    if len(_ws_rate_limits[user_id]) >= WS_RATE_LIMIT:
        return False
    _ws_rate_limits[user_id].append(now)
    return True


@router.websocket("/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for real-time multiplayer features.
    Requires authentication via token query parameter.
    """
    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return
    
    try:
        # Authenticate user from token
        user = await get_current_user_from_token(token, db)
        if not user:
            await websocket.close(code=4001, reason="Invalid authentication token")
            return
        
        # Get player data
        player = db.query(Player).filter(Player.user_id == user.id).first()
        if not player:
            await websocket.close(code=4002, reason="Player profile not found")
            return
        
        # Prepare user data for connection
        user_data = {
            "user_id": str(user.id),
            "username": user.username,
            "player_id": str(player.id),
            "current_sector": player.current_sector_id,
            # WO-DBB-RT4: pass the region so connect() joins the region room (broadcast_to_region).
            "current_region_id": str(player.current_region_id) if getattr(player, "current_region_id", None) else None,
            "team_id": str(player.team_id) if player.team_id else None,
            "credits": player.credits,
            "turns": player.turns,
            # Reputation and Ranking for Comms display
            "personal_reputation": player.personal_reputation,
            "reputation_tier": player.reputation_tier,
            "name_color": player.name_color,
            "military_rank": player.military_rank
        }
        
        # Connect to WebSocket manager
        await connection_manager.connect(websocket, str(user.id), user_data)
        
        try:
            while True:
                # Wait for messages from client
                data = await websocket.receive_text()

                # Rate limit: 100 msg/s per connection
                if not _check_ws_rate_limit(str(user.id)):
                    await connection_manager.send_personal_message(str(user.id), {
                        "type": "error",
                        "message": "Rate limit exceeded. Max 100 messages per second."
                    })
                    continue

                try:
                    message_data = json.loads(data)
                    await handle_websocket_message(str(user.id), message_data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from user {user.id}")
                    await connection_manager.send_personal_message(str(user.id), {
                        "type": "error",
                        "message": "Invalid message format"
                    })
                except Exception as e:
                    logger.error(f"Error handling WebSocket message from user {user.id}: {e}")
                    await connection_manager.send_personal_message(str(user.id), {
                        "type": "error",
                        "message": "Error processing message"
                    })
        
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for user {user.id}")
        except Exception as e:
            logger.error(f"WebSocket error for user {user.id}: {e}")
        finally:
            # Pass our own socket: if we were the one evicted (superseded by
            # a newer connection for this user), disconnect() no-ops instead
            # of scrubbing the successor's registration (WO-RT-EVICTION-SUPERSEDE).
            await connection_manager.disconnect(str(user.id), websocket)
    
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        try:
            await websocket.close(code=4000, reason="Connection error")
        except Exception as e:
            logger.warning(f"Failed to close WebSocket cleanly: {e}")


@router.websocket("/admin")
async def admin_websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Admin WebSocket endpoint for real-time admin dashboard updates.
    Requires admin authentication via token query parameter.
    """
    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return
    
    try:
        # Authenticate admin user from token
        user = await get_current_user_from_token(token, db)
        if not user or not user.is_admin:
            await websocket.close(code=4001, reason="Admin authentication required")
            return
        
        # Prepare admin data for connection
        admin_data = {
            "user_id": str(user.id),
            "username": user.username,
            "is_admin": True
        }
        
        # Connect to WebSocket manager
        await connection_manager.connect_admin(websocket, str(user.id), admin_data)
        
        try:
            while True:
                # Wait for messages from client
                data = await websocket.receive_text()
                
                try:
                    message_data = json.loads(data)
                    await handle_admin_websocket_message(str(user.id), message_data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from admin {user.id}: {data}")
                    await connection_manager.send_admin_message(str(user.id), {
                        "type": "error",
                        "message": "Invalid message format",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                except Exception as e:
                    logger.error(f"Error handling admin WebSocket message from {user.id}: {e}")
                    await connection_manager.send_admin_message(str(user.id), {
                        "type": "error",
                        "message": "Error processing message",
                        "timestamp": datetime.utcnow().isoformat()
                    })
        
        except WebSocketDisconnect:
            logger.info(f"Admin WebSocket disconnected for {user.id}")
        except Exception as e:
            logger.error(f"Admin WebSocket error for {user.id}: {e}")
        finally:
            # Pass our own socket: if we were the one evicted (superseded by
            # a newer connection for this admin), disconnect_admin() no-ops
            # instead of scrubbing the successor's registration (WO-RT-ADMIN-EVICTION).
            await connection_manager.disconnect_admin(str(user.id), websocket)
                
    except Exception as e:
        logger.error(f"Admin WebSocket connection error: {str(e)}")
        try:
            await websocket.close(code=4003, reason="Connection initialization failed")
        except Exception:
            pass




@router.get("/stats")
async def get_websocket_stats(
    current_user: User = Depends(get_current_admin_user)
) -> dict:
    """Get WebSocket connection statistics (admin only)"""
    return connection_manager.get_connection_stats()


@router.post("/broadcast")
async def broadcast_message(
    request: BroadcastRequest,
    target_type: str = "global",  # global, sector, team
    target_id: Optional[str] = None,
    current_user: User = Depends(get_current_admin_user)
) -> dict:
    """Broadcast a message to connected users (admin only)"""

    message = {
        "type": "admin_broadcast",
        "content": request.content,
        "from": "System Administrator",
        "priority": request.priority,
        "timestamp": datetime.utcnow().isoformat(),
    }
    
    if target_type == "global":
        await connection_manager.broadcast_global(message)
    elif target_type == "sector" and target_id:
        await connection_manager.broadcast_to_sector(int(target_id), message)
    elif target_type == "team" and target_id:
        await connection_manager.broadcast_to_team(target_id, message)
    else:
        raise HTTPException(status_code=400, detail="Invalid target type or missing target_id")

    # Audit log admin broadcasts
    logger.info(
        "ADMIN_BROADCAST: admin_id=%s target=%s:%s content_length=%d",
        current_user.id, target_type, target_id or "all",
        len(request.content),
    )

    return {"message": "Broadcast sent successfully", "target_type": target_type, "target_id": target_id}


@router.get("/sector/{sector_id}/players")
async def get_sector_players(
    sector_id: int,
    current_user: User = Depends(get_current_admin_user)
) -> dict:
    """Get list of players currently in a specific sector"""
    players = connection_manager.get_sector_players(sector_id)
    return {
        "sector_id": sector_id,
        "players": players,
        "count": len(players)
    }


@router.get("/team/{team_id}/players")
async def get_team_players(
    team_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
) -> dict:
    """Get list of online players in a specific team"""
    
    players = connection_manager.get_team_players(team_id)
    return {
        "team_id": team_id,
        "players": players,
        "count": len(players)
    }