import json
import asyncio
from typing import Dict, List, Set, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime, UTC
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for real-time multiplayer features"""
    
    def __init__(self):
        # Store active connections by user ID
        self.active_connections: Dict[str, WebSocket] = {}
        # Store user metadata for each connection
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
        # Store connections by sector for location-based updates
        self.sector_connections: Dict[int, Set[str]] = {}
        # Store connections by team for team-based communication
        self.team_connections: Dict[str, Set[str]] = {}
        # Store admin connections separately
        self.admin_connections: Dict[str, WebSocket] = {}
        self.admin_metadata: Dict[str, Dict[str, Any]] = {}
        
    async def connect(self, websocket: WebSocket, user_id: str, user_data: Dict[str, Any]):
        """Accept a new WebSocket connection"""
        await websocket.accept()
        
        # If user already connected, disconnect old connection
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].close()
            except Exception as e:
                logger.warning(f"Error closing existing connection for user {user_id}: {e}")
        
        # Store new connection
        self.active_connections[user_id] = websocket
        self.connection_metadata[user_id] = {
            "connected_at": datetime.now(UTC),
            "user_data": user_data,
            "current_sector": user_data.get("current_sector"),
            "team_id": user_data.get("team_id"),
            "last_heartbeat": datetime.now(UTC)
        }
        
        # Add to sector connections if user has a location
        current_sector = user_data.get("current_sector")
        if current_sector:
            if current_sector not in self.sector_connections:
                self.sector_connections[current_sector] = set()
            self.sector_connections[current_sector].add(user_id)
        
        # Add to team connections if user has a team
        team_id = user_data.get("team_id")
        if team_id:
            if team_id not in self.team_connections:
                self.team_connections[team_id] = set()
            self.team_connections[team_id].add(user_id)
        
        logger.info(f"User {user_id} connected via WebSocket")
        
        # Notify other players in the same sector
        if current_sector:
            await self.broadcast_to_sector(current_sector, {
                "type": "player_entered_sector",
                "user_id": user_id,
                "username": user_data.get("username"),
                "sector_id": current_sector,
                "timestamp": datetime.now(UTC).isoformat()
            }, exclude_user=user_id)
    
    async def disconnect(self, user_id: str):
        """Remove a WebSocket connection"""
        if user_id not in self.active_connections:
            return
        
        metadata = self.connection_metadata.get(user_id, {})
        current_sector = metadata.get("current_sector")
        team_id = metadata.get("team_id")
        
        # Remove from active connections
        del self.active_connections[user_id]
        del self.connection_metadata[user_id]
        
        # Remove from sector connections
        if current_sector and current_sector in self.sector_connections:
            self.sector_connections[current_sector].discard(user_id)
            if not self.sector_connections[current_sector]:
                del self.sector_connections[current_sector]
        
        # Remove from team connections
        if team_id and team_id in self.team_connections:
            self.team_connections[team_id].discard(user_id)
            if not self.team_connections[team_id]:
                del self.team_connections[team_id]
        
        logger.info(f"User {user_id} disconnected from WebSocket")
        
        # Notify other players in the same sector
        if current_sector:
            await self.broadcast_to_sector(current_sector, {
                "type": "player_left_sector",
                "user_id": user_id,
                "username": metadata.get("user_data", {}).get("username"),
                "sector_id": current_sector,
                "timestamp": datetime.now(UTC).isoformat()
            })
    
    async def send_personal_message(self, user_id: str, message: Dict[str, Any]):
        """Send a message to a specific user"""
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send_text(json.dumps(message))
                return True
            except Exception as e:
                logger.error(f"Error sending message to user {user_id}: {e}")
                await self.disconnect(user_id)
        return False
    
    async def broadcast_to_sector(self, sector_id: int, message: Dict[str, Any], exclude_user: Optional[str] = None):
        """Broadcast a message to all users in a specific sector"""
        if sector_id not in self.sector_connections:
            return
        
        # Add sector context to message
        message["sector_id"] = sector_id
        
        disconnect_users = []
        for user_id in self.sector_connections[sector_id]:
            if exclude_user and user_id == exclude_user:
                continue
            
            try:
                await self.active_connections[user_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting to user {user_id} in sector {sector_id}: {e}")
                disconnect_users.append(user_id)
        
        # Clean up failed connections
        for user_id in disconnect_users:
            await self.disconnect(user_id)
    
    async def broadcast_to_team(self, team_id: str, message: Dict[str, Any], exclude_user: Optional[str] = None):
        """Broadcast a message to all users in a specific team"""
        if team_id not in self.team_connections:
            return
        
        # Add team context to message
        message["team_id"] = team_id
        
        disconnect_users = []
        for user_id in self.team_connections[team_id]:
            if exclude_user and user_id == exclude_user:
                continue
            
            try:
                await self.active_connections[user_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting to user {user_id} in team {team_id}: {e}")
                disconnect_users.append(user_id)
        
        # Clean up failed connections
        for user_id in disconnect_users:
            await self.disconnect(user_id)
    
    async def broadcast_global(self, message: Dict[str, Any], exclude_user: Optional[str] = None):
        """Broadcast a message to all connected users"""
        disconnect_users = []
        for user_id in list(self.active_connections.keys()):
            if exclude_user and user_id == exclude_user:
                continue
            
            try:
                await self.active_connections[user_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting globally to user {user_id}: {e}")
                disconnect_users.append(user_id)
        
        # Clean up failed connections
        for user_id in disconnect_users:
            await self.disconnect(user_id)
    
    # Real-time game event methods requested by UI teams
    
    async def send_combat_update(self, combat_id: str, combat_data: Dict[str, Any], participants: List[str] = None):
        """Send combat update to participants or global if no participants specified"""
        message = {
            "type": "combat_update",
            "combat_id": combat_id,
            "timestamp": datetime.now(UTC).isoformat(),
            **combat_data
        }
        
        if participants:
            # Send to specific participants
            for user_id in participants:
                await self.send_personal_message(user_id, message)
        else:
            # Broadcast globally for major events
            await self.broadcast_global(message)
    
    async def send_new_message_notification(self, recipient_user_id: str, message_data: Dict[str, Any]):
        """Send new message notification to a specific user"""
        notification = {
            "type": "new_message",
            "timestamp": datetime.now(UTC).isoformat(),
            **message_data
        }
        await self.send_personal_message(recipient_user_id, notification)
    
    async def send_ship_status_change(self, user_id: str, ship_data: Dict[str, Any]):
        """Send ship status change to ship owner"""
        message = {
            "type": "ship_status_change",
            "timestamp": datetime.now(UTC).isoformat(),
            **ship_data
        }
        await self.send_personal_message(user_id, message)
    
    async def send_economy_alert(self, alert_data: Dict[str, Any], admin_only: bool = True):
        """Send economy alert to admins or all users"""
        message = {
            "type": "economy:alert",
            "timestamp": datetime.now(UTC).isoformat(),
            **alert_data
        }
        
        if admin_only:
            # Send to all connected admins
            await self.broadcast_to_admins(message)
        else:
            # Broadcast to all users
            await self.broadcast_global(message)
    
    async def send_fleet_update(self, fleet_id: str, fleet_data: Dict[str, Any], team_id: str = None):
        """Send fleet update to team members or global"""
        message = {
            "type": "fleet_update",
            "fleet_id": fleet_id,
            "timestamp": datetime.now(UTC).isoformat(),
            **fleet_data
        }
        
        if team_id:
            await self.broadcast_to_team(team_id, message)
        else:
            await self.broadcast_global(message)
    
    async def send_market_update(self, market_data: Dict[str, Any], sector_id: int = None):
        """Send market/trading update to sector or global"""
        message = {
            "type": "market_update",
            "timestamp": datetime.now(UTC).isoformat(),
            **market_data
        }
        
        if sector_id:
            await self.broadcast_to_sector(sector_id, message)
        else:
            await self.broadcast_global(message)
    
    async def send_planetary_update(self, planet_data: Dict[str, Any], owner_user_id: str = None, sector_id: int = None):
        """Send planetary update to planet owner or sector"""
        message = {
            "type": "planetary_update",
            "timestamp": datetime.now(UTC).isoformat(),
            **planet_data
        }
        
        if owner_user_id:
            await self.send_personal_message(owner_user_id, message)
        elif sector_id:
            await self.broadcast_to_sector(sector_id, message)
    
    async def connect_admin(self, websocket: WebSocket, admin_id: str, admin_data: Dict[str, Any]):
        """Accept a new admin WebSocket connection"""
        await websocket.accept()
        
        # If admin already connected, disconnect old connection
        if admin_id in self.admin_connections:
            try:
                await self.admin_connections[admin_id].close()
            except Exception as e:
                logger.warning(f"Error closing existing admin connection for {admin_id}: {e}")
        
        # Store new admin connection
        self.admin_connections[admin_id] = websocket
        self.admin_metadata[admin_id] = {
            "connected_at": datetime.now(UTC),
            "admin_data": admin_data,
            "last_heartbeat": datetime.now(UTC),
            "subscriptions": set()  # Track what events this admin wants
        }
        
        logger.info(f"Admin {admin_id} ({admin_data.get('username')}) connected via WebSocket")
        
        # Send initial stats
        await self.send_admin_message(admin_id, {
            "type": "connection_established",
            "stats": self.get_connection_stats(),
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    async def disconnect_admin(self, admin_id: str):
        """Remove an admin WebSocket connection"""
        if admin_id not in self.admin_connections:
            return
        
        # Remove from active connections
        del self.admin_connections[admin_id]
        del self.admin_metadata[admin_id]
        
        logger.info(f"Admin {admin_id} disconnected from WebSocket")
    
    async def send_admin_message(self, admin_id: str, message: Dict[str, Any]):
        """Send a message to a specific admin"""
        if admin_id in self.admin_connections:
            try:
                await self.admin_connections[admin_id].send_text(json.dumps(message))
                return True
            except Exception as e:
                logger.error(f"Error sending message to admin {admin_id}: {e}")
                await self.disconnect_admin(admin_id)
        return False
    
    async def broadcast_to_admins(self, message: Dict[str, Any], exclude_admin: Optional[str] = None):
        """Broadcast a message to all connected admins"""
        disconnect_admins = []
        for admin_id in list(self.admin_connections.keys()):
            if exclude_admin and admin_id == exclude_admin:
                continue
            
            try:
                await self.admin_connections[admin_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting to admin {admin_id}: {e}")
                disconnect_admins.append(admin_id)
        
        # Clean up failed connections
        for admin_id in disconnect_admins:
            await self.disconnect_admin(admin_id)
    
    async def send_admin_intervention_alert(self, intervention_data: Dict[str, Any]):
        """Send admin intervention alert to all admins"""
        message = {
            "type": "admin_intervention",
            "timestamp": datetime.now(UTC).isoformat(),
            **intervention_data
        }
        
        # Send to all connected admins
        await self.broadcast_to_admins(message)
    
    async def send_real_time_update(self, event_type: str, event_data: Dict[str, Any], target_admins: bool = True):
        """Send real-time updates with proper event type formatting"""
        # Convert event type to colon-separated format for admin UI
        # e.g., "combat_new_event" -> "combat:new-event"
        formatted_type = event_type.replace('_', ':').replace(':', '-', 1).replace('-', ':')
        
        message = {
            "type": formatted_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **event_data
        }
        
        # Send to admins if requested
        if target_admins:
            await self.broadcast_to_admins(message)
        
        # Also send to regular users for certain events
        if event_type in ['combat_update', 'economy_update', 'fleet_update']:
            await self.broadcast_global(message)
    
    async def update_user_location(self, user_id: str, new_sector_id: int):
        """Update a user's sector location and notify relevant players"""
        if user_id not in self.connection_metadata:
            return
        
        metadata = self.connection_metadata[user_id]
        old_sector_id = metadata.get("current_sector")
        
        # Remove from old sector
        if old_sector_id and old_sector_id in self.sector_connections:
            self.sector_connections[old_sector_id].discard(user_id)
            if not self.sector_connections[old_sector_id]:
                del self.sector_connections[old_sector_id]
            
            # Notify players in old sector
            await self.broadcast_to_sector(old_sector_id, {
                "type": "player_left_sector",
                "user_id": user_id,
                "username": metadata.get("user_data", {}).get("username"),
                "sector_id": old_sector_id,
                "timestamp": datetime.now(UTC).isoformat()
            })
        
        # Add to new sector
        if new_sector_id not in self.sector_connections:
            self.sector_connections[new_sector_id] = set()
        self.sector_connections[new_sector_id].add(user_id)
        
        # Update metadata
        metadata["current_sector"] = new_sector_id
        
        # Notify players in new sector
        await self.broadcast_to_sector(new_sector_id, {
            "type": "player_entered_sector",
            "user_id": user_id,
            "username": metadata.get("user_data", {}).get("username"),
            "sector_id": new_sector_id,
            "timestamp": datetime.now(UTC).isoformat()
        }, exclude_user=user_id)
        
        # Notify the moving player about other players in the new sector
        other_players = []
        for other_user_id in self.sector_connections[new_sector_id]:
            if other_user_id != user_id:
                other_metadata = self.connection_metadata.get(other_user_id, {})
                other_players.append({
                    "user_id": other_user_id,
                    "username": other_metadata.get("user_data", {}).get("username"),
                    "connected_at": other_metadata.get("connected_at", datetime.now(UTC)).isoformat()
                })
        
        await self.send_personal_message(user_id, {
            "type": "sector_entered",
            "sector_id": new_sector_id,
            "other_players": other_players,
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    def get_sector_players(self, sector_id: int) -> List[Dict[str, Any]]:
        """Get list of players currently in a sector"""
        if sector_id not in self.sector_connections:
            return []

        players = []
        for user_id in self.sector_connections[sector_id]:
            metadata = self.connection_metadata.get(user_id, {})
            user_data = metadata.get("user_data", {})
            players.append({
                "user_id": user_id,
                "username": user_data.get("username"),
                "connected_at": metadata.get("connected_at", datetime.now(UTC)).isoformat(),
                "last_heartbeat": metadata.get("last_heartbeat", datetime.now(UTC)).isoformat(),
                # Reputation and Ranking for Comms display
                "personal_reputation": user_data.get("personal_reputation", 0),
                "reputation_tier": user_data.get("reputation_tier", "Neutral"),
                "name_color": user_data.get("name_color", "#FFFFFF"),
                "military_rank": user_data.get("military_rank", "Recruit")
            })

        return players
    
    def get_team_players(self, team_id: str) -> List[Dict[str, Any]]:
        """Get list of players currently online in a team"""
        if team_id not in self.team_connections:
            return []
        
        players = []
        for user_id in self.team_connections[team_id]:
            metadata = self.connection_metadata.get(user_id, {})
            user_data = metadata.get("user_data", {})
            players.append({
                "user_id": user_id,
                "username": user_data.get("username"),
                "current_sector": metadata.get("current_sector"),
                "connected_at": metadata.get("connected_at", datetime.now(UTC)).isoformat(),
                "last_heartbeat": metadata.get("last_heartbeat", datetime.now(UTC)).isoformat()
            })
        
        return players
    
    async def handle_heartbeat(self, user_id: str):
        """Update last heartbeat for a user"""
        if user_id in self.connection_metadata:
            self.connection_metadata[user_id]["last_heartbeat"] = datetime.now(UTC)
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get statistics about current connections"""
        return {
            "total_connections": len(self.active_connections),
            "total_admin_connections": len(self.admin_connections),
            "sectors_with_players": len(self.sector_connections),
            "teams_with_players": len(self.team_connections),
            "connections_by_sector": {
                sector_id: len(users) for sector_id, users in self.sector_connections.items()
            },
            "connections_by_team": {
                team_id: len(users) for team_id, users in self.team_connections.items()
            }
        }
    
    async def handle_admin_heartbeat(self, admin_id: str):
        """Update last heartbeat for an admin"""
        if admin_id in self.admin_metadata:
            self.admin_metadata[admin_id]["last_heartbeat"] = datetime.now(UTC)
    
    async def update_admin_subscriptions(self, admin_id: str, event_types: List[str]):
        """Update which events an admin wants to receive"""
        # Whitelist allowed admin event types
        ALLOWED_ADMIN_EVENTS = {
            "combat_update", "economy_update", "fleet_update",
            "player_statistics", "sector_update", "system_alert",
        }
        filtered = [e for e in event_types if e in ALLOWED_ADMIN_EVENTS]
        if admin_id in self.admin_metadata:
            self.admin_metadata[admin_id]["subscriptions"] = set(filtered)
            logger.info(f"Admin {admin_id} subscribed to events: {filtered}")

    async def cleanup_stale_connections(self, timeout_seconds: int = 300):
        """Disconnect connections that haven't sent a heartbeat within timeout.
        Should be called periodically (e.g., every 30 seconds from a background task).
        """
        now = datetime.now(UTC)
        stale_users = []

        for user_id, metadata in list(self.connection_metadata.items()):
            last_hb = metadata.get("last_heartbeat")
            if last_hb and (now - last_hb).total_seconds() > timeout_seconds:
                stale_users.append(user_id)

        for user_id in stale_users:
            logger.info(f"Disconnecting stale WebSocket: user {user_id} (heartbeat timeout)")
            ws = self.active_connections.get(user_id)
            if ws:
                try:
                    await ws.close(code=4008, reason="Heartbeat timeout")
                except Exception:
                    pass
            await self.disconnect(user_id)

        # Also clean stale admins
        stale_admins = []
        for admin_id, metadata in list(self.admin_metadata.items()):
            last_hb = metadata.get("last_heartbeat")
            if last_hb and (now - last_hb).total_seconds() > timeout_seconds:
                stale_admins.append(admin_id)

        for admin_id in stale_admins:
            logger.info(f"Disconnecting stale admin WebSocket: {admin_id}")
            ws = self.admin_connections.get(admin_id)
            if ws:
                try:
                    await ws.close(code=4008, reason="Heartbeat timeout")
                except Exception:
                    pass
            await self.disconnect_admin(admin_id)

        if stale_users or stale_admins:
            logger.info(f"Cleaned up {len(stale_users)} stale user + {len(stale_admins)} admin connections")

        return len(stale_users) + len(stale_admins)


# Global connection manager instance
connection_manager = ConnectionManager()


async def handle_websocket_message(user_id: str, message_data: Dict[str, Any]):
    """Handle incoming WebSocket messages from clients"""
    message_type = message_data.get("type")
    
    if message_type == "heartbeat":
        await connection_manager.handle_heartbeat(user_id)
        await connection_manager.send_personal_message(user_id, {
            "type": "heartbeat_ack",
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    elif message_type == "chat_message":
        # Handle chat messages
        target_type = message_data.get("target_type", "sector")  # sector, team, global
        content = message_data.get("content", "")
        
        if not content.strip():
            return
        
        metadata = connection_manager.connection_metadata.get(user_id, {})
        user_data = metadata.get("user_data", {})
        
        chat_message = {
            "type": "chat_message",
            "from_user_id": user_id,
            "from_username": user_data.get("username", "Unknown"),
            "content": content,
            "target_type": target_type,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        if target_type == "sector":
            current_sector = metadata.get("current_sector")
            if current_sector:
                await connection_manager.broadcast_to_sector(current_sector, chat_message, exclude_user=user_id)
        
        elif target_type == "team":
            team_id = metadata.get("team_id")
            if team_id:
                # Revalidate team membership before broadcasting
                # (player may have been removed since WebSocket connected)
                if user_id in connection_manager.team_connections.get(team_id, set()):
                    await connection_manager.broadcast_to_team(team_id, chat_message, exclude_user=user_id)
                else:
                    await connection_manager.send_personal_message(user_id, {
                        "type": "error",
                        "message": "You are no longer a member of this team"
                    })
        
        elif target_type == "global":
            await connection_manager.broadcast_global(chat_message, exclude_user=user_id)
    
    elif message_type == "request_sector_players":
        # Send list of players in current sector
        metadata = connection_manager.connection_metadata.get(user_id, {})
        current_sector = metadata.get("current_sector")
        if current_sector:
            players = connection_manager.get_sector_players(current_sector)
            await connection_manager.send_personal_message(user_id, {
                "type": "sector_players",
                "sector_id": current_sector,
                "players": players,
                "timestamp": datetime.now(UTC).isoformat()
            })
    
    elif message_type == "request_team_players":
        # Send list of online team members
        metadata = connection_manager.connection_metadata.get(user_id, {})
        team_id = metadata.get("team_id")
        if team_id:
            players = connection_manager.get_team_players(team_id)
            await connection_manager.send_personal_message(user_id, {
                "type": "team_players",
                "team_id": team_id,
                "players": players,
                "timestamp": datetime.now(UTC).isoformat()
            })
    
    elif message_type == "aria_chat":
        await handle_aria_chat(user_id, message_data)

    else:
        logger.warning(f"Unknown WebSocket message type: {message_type} from user {user_id}")


async def handle_aria_chat(user_id: str, message_data: Dict[str, Any]):
    """Route a player's ARIA chat message to the AI service and return an
    aria_response. The plain WS endpoint uses the sync connection manager, but
    EnhancedAIService needs an AsyncSession, so we open one here. AI safety —
    input sanitization, prompt-injection filtering, and response sanitization —
    lives INSIDE EnhancedAIService.process_natural_language_query (the same
    proven path the /enhanced-ai/chat REST route uses); we do not bypass it."""
    import uuid as _uuid

    metadata = connection_manager.connection_metadata.get(user_id, {})
    user_data = metadata.get("user_data", {})
    player_id = user_data.get("player_id")
    content = (message_data.get("content") or "").strip()
    conversation_id = message_data.get("conversation_id")
    context_type = message_data.get("context") or "query"

    if not player_id or not content:
        await connection_manager.send_personal_message(user_id, {
            "type": "error",
            "message": "ARIA request missing player context or message content",
        })
        return

    try:
        from src.core.database import AsyncSessionLocal
        from src.services.enhanced_ai_service import EnhancedAIService, ConversationContext
        from src.models.enhanced_ai_models import SecurityLevel

        async with AsyncSessionLocal() as adb:
            ai_service = EnhancedAIService(adb)
            # Continue an existing thread when the client supplies a valid id;
            # otherwise the service mints a fresh conversation context.
            conversation_context = None
            if conversation_id:
                try:
                    conversation_context = ConversationContext(
                        session_id=conversation_id,
                        conversation_type=context_type,
                        player_id=str(player_id),
                        assistant_id="",  # populated by the service
                        security_level=SecurityLevel.STANDARD,
                    )
                except ValueError:
                    conversation_context = None

            result = await ai_service.process_natural_language_query(
                player_id=_uuid.UUID(str(player_id)),
                user_input=content,
                conversation_context=conversation_context,
            )
            await adb.commit()

        await connection_manager.send_personal_message(user_id, {
            "type": "aria_response",
            "conversation_id": result.get("conversation_id"),
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {
                "message": result.get("response", ""),
                "confidence": 0.95,
                "context_used": context_type,
                "actions": [],
                "suggestions": [],
                "learning_note": None,
            },
        })
    except Exception as e:
        logger.error(f"ARIA chat handling failed for user {user_id}: {e}")
        await connection_manager.send_personal_message(user_id, {
            "type": "aria_response",
            "conversation_id": conversation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {
                "message": "ARIA is temporarily unavailable. Please try again.",
                "confidence": 0,
                "context_used": context_type,
                "actions": [],
                "suggestions": [],
                "learning_note": None,
            },
        })


async def handle_admin_websocket_message(admin_id: str, message_data: Dict[str, Any]):
    """Handle incoming WebSocket messages from admin clients"""
    message_type = message_data.get("type")
    
    if message_type == "heartbeat":
        await connection_manager.handle_admin_heartbeat(admin_id)
        await connection_manager.send_admin_message(admin_id, {
            "type": "pong",
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    elif message_type == "subscribe":
        # Admin subscribing to specific event types
        event_types = message_data.get("events", [])
        await connection_manager.update_admin_subscriptions(admin_id, event_types)
        await connection_manager.send_admin_message(admin_id, {
            "type": "subscription_confirmed",
            "events": event_types,
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    elif message_type == "request_stats":
        # Send current connection stats
        stats = connection_manager.get_connection_stats()
        await connection_manager.send_admin_message(admin_id, {
            "type": "system:stats",
            "data": stats,
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    elif message_type == "broadcast":
        # Admin broadcasting a message
        content = message_data.get("content", "")
        target = message_data.get("target", "global")
        
        broadcast_msg = {
            "type": "system:announcement",
            "content": content,
            "from": "System Administrator",
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        if target == "global":
            await connection_manager.broadcast_global(broadcast_msg)
        
        await connection_manager.send_admin_message(admin_id, {
            "type": "broadcast_sent",
            "target": target,
            "timestamp": datetime.now(UTC).isoformat()
        })
    
    else:
        logger.warning(f"Unknown admin WebSocket message type: {message_type} from admin {admin_id}")