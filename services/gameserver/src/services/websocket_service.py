import json
import asyncio
from typing import Dict, List, Set, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime, UTC
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)

# NO-CANON: no canon prescribes a topic-name length cap; this is a conservative
# bound so a client-supplied topic name (subscribe_topic) cannot bloat the
# in-memory registry key. Reported to the Orchestrator.
MAX_TOPIC_NAME_LENGTH = 128

# Canon: SYSTEMS/realtime-bus.md "Rate limits" table — "Topic subscriptions
# per socket | 50 | binding" — and the "Subscription spam" failure mode:
# "Server caps subscriptions per socket (e.g. 50 topics); excess returns
# subscription_rejected." (WO-RT-BUS-HARDENING)
MAX_TOPICS_PER_USER = 50


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
        # Store connections by region for region-scoped events (WO-DBB-RT4):
        # governance / election / treaty broadcasts. Keyed by region_id (str),
        # mirroring sector_connections / team_connections exactly.
        self.region_connections: Dict[str, Set[str]] = {}
        # Generic topic pub/sub (WO-DBB-RT5): topic name -> set of subscribed
        # user_ids. Lets any service fan out to arbitrary topic subscribers via
        # publish_topic(), not just the sector/team/market firehoses. Players
        # opt in with subscribe_topic(); subscriptions are torn down on
        # disconnect so a topic set never references a dead connection.
        self.topic_subscriptions: Dict[str, Set[str]] = {}
        # Store admin connections separately
        self.admin_connections: Dict[str, WebSocket] = {}
        self.admin_metadata: Dict[str, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket, user_id: str, user_data: Dict[str, Any]):
        """Accept a new WebSocket connection"""
        await websocket.accept()
        
        # If user already connected, evict the old connection. Close with
        # 4001/reason="superseded" (canon reuses 4001 for both auth failure
        # and eviction — realtime-bus.md:70 — the reason string is the
        # discriminator) so the client can tell this apart from an auth
        # rejection and skip its reconnect-with-refresh path
        # (WO-RT-EVICTION-SUPERSEDE). The route's finally block passes its
        # own socket to disconnect(), so the evicted handler can't turn
        # around and scrub this new connection's registration.
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].close(code=4001, reason="superseded")
            except Exception as e:
                logger.warning(f"Error closing existing connection for user {user_id}: {e}")
        
        # Store new connection
        self.active_connections[user_id] = websocket
        # Region scope (WO-DBB-RT4): accept either "current_region" or the
        # player-model field name "current_region_id"; normalize to a str so
        # the region_connections key type is stable. None until a caller
        # supplies region context (additive — older callers stay unaffected).
        current_region_raw = user_data.get("current_region") or user_data.get("current_region_id")
        current_region = str(current_region_raw) if current_region_raw is not None else None
        self.connection_metadata[user_id] = {
            "connected_at": datetime.now(UTC),
            "user_data": user_data,
            "current_sector": user_data.get("current_sector"),
            "team_id": user_data.get("team_id"),
            "current_region": current_region,
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

        # Add to region connections if user has a region (WO-DBB-RT4)
        if current_region:
            if current_region not in self.region_connections:
                self.region_connections[current_region] = set()
            self.region_connections[current_region].add(user_id)

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
    
    async def disconnect(self, user_id: str, websocket: Optional[WebSocket] = None) -> bool:
        """Remove a WebSocket connection.

        `websocket`, when passed, must be the exact socket the caller owns
        (the route's finally block passes its own connection object). If a
        newer socket has since replaced it in active_connections, this is a
        no-op — an evicted handler's finally must never scrub the socket
        that superseded it (WO-RT-EVICTION-SUPERSEDE). Internal prune paths
        that only know the user_id (e.g. send-failure cleanup) call this
        with websocket=None and keep the prior unconditional behavior.

        Returns True iff this call actually performed the teardown (identity
        matched, or no identity was given), False on the no-op/stale-handler
        path. WO-RT-BUS-HARDENING: the route uses this to gate pruning its
        own per-user rate-limit/violation dicts — a superseded handler's
        finally must not wipe out the state a live successor connection has
        already started accumulating (the same eviction race this identity
        check was built to close, applied to a second piece of per-user state).
        """
        if user_id not in self.active_connections:
            return False
        if websocket is not None and self.active_connections[user_id] is not websocket:
            return False

        metadata = self.connection_metadata.get(user_id, {})
        current_sector = metadata.get("current_sector")
        team_id = metadata.get("team_id")
        current_region = metadata.get("current_region")

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

        # Remove from region connections (WO-DBB-RT4)
        if current_region and current_region in self.region_connections:
            self.region_connections[current_region].discard(user_id)
            if not self.region_connections[current_region]:
                del self.region_connections[current_region]

        # Remove from every topic subscription (WO-DBB-RT5) so no topic set
        # ever references a dead connection.
        for topic in list(self.topic_subscriptions.keys()):
            subscribers = self.topic_subscriptions[topic]
            if user_id in subscribers:
                subscribers.discard(user_id)
                if not subscribers:
                    del self.topic_subscriptions[topic]

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

        return True

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
    
    async def broadcast_to_region(self, region_id: str, message: Dict[str, Any], exclude: Optional[str] = None):
        """Broadcast a message to all connected users in a specific region.

        WO-DBB-RT4 — the region-room primitive (none existed before). The
        foundation for governance / election / treaty events, which are
        region-scoped: every member of region R should see them, no one in
        another region S should. Mirrors broadcast_to_sector /
        broadcast_to_team exactly (region context stamped on the message, failed
        sends pruned via disconnect). ``region_id`` is normalized to str to
        match the region_connections key type; ``exclude`` skips one user_id
        (e.g. the actor who triggered the event)."""
        region_key = str(region_id) if region_id is not None else None
        if region_key is None or region_key not in self.region_connections:
            return

        # Add region context to message
        message["region_id"] = region_key

        disconnect_users = []
        for user_id in list(self.region_connections[region_key]):
            if exclude and user_id == exclude:
                continue

            ws = self.active_connections.get(user_id)
            if ws is None:
                disconnect_users.append(user_id)
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting to user {user_id} in region {region_key}: {e}")
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

    # --- Generic topic pub/sub (WO-DBB-RT5) --------------------------------

    def count_topic_subscriptions(self, user_id: str) -> int:
        """Count how many distinct topics user_id is currently subscribed to.

        O(topics), not O(1) — no reverse per-user index exists (mirrors the
        rest of this registry, which is all topic -> set-of-users). Topic
        counts are small enough in practice that this is fine; introduce a
        reverse index only if profiling says otherwise."""
        return sum(1 for subscribers in self.topic_subscriptions.values() if user_id in subscribers)

    def subscribe_topic(self, user_id: str, topic: str) -> bool:
        """Subscribe a connected user to a generic topic.

        WO-DBB-RT5 — opt a user into a named topic so a later publish_topic()
        reaches them. Idempotent (a set). Only subscribes already-connected
        users so a topic set never references a dead connection; teardown lives
        in disconnect(). Mirrors the team_connections registry idiom (a
        topic -> set-of-user_ids map).

        WO-RT-BUS-HARDENING: enforces canon's MAX_TOPICS_PER_USER cap (50,
        binding per SYSTEMS/realtime-bus.md's Rate limits table). A topic the
        user is already subscribed to never consumes a fresh slot (idempotent
        re-subscribe always succeeds). Returns True if the subscription is
        registered (new or already-present), False if the cap was hit — the
        caller (handle_websocket_message) sends a subscription_rejected frame
        and does not register."""
        if not topic or user_id not in self.active_connections:
            return False
        if topic in self.topic_subscriptions and user_id in self.topic_subscriptions[topic]:
            return True
        if self.count_topic_subscriptions(user_id) >= MAX_TOPICS_PER_USER:
            return False
        if topic not in self.topic_subscriptions:
            self.topic_subscriptions[topic] = set()
        self.topic_subscriptions[topic].add(user_id)
        return True

    def unsubscribe_topic(self, user_id: str, topic: str):
        """Unsubscribe a user from a generic topic (WO-DBB-RT5)."""
        subscribers = self.topic_subscriptions.get(topic)
        if subscribers is None:
            return
        subscribers.discard(user_id)
        if not subscribers:
            del self.topic_subscriptions[topic]

    async def publish_topic(self, topic: str, message: Dict[str, Any], exclude: Optional[str] = None):
        """Publish a message to every subscriber of a generic topic.

        WO-DBB-RT5 — the generic fan-out primitive over the topic-subscription
        registry, so any service can fan out to arbitrary topic subscribers
        (not just the sector/team/market firehoses). Only that topic's
        subscribers receive the frame; non-subscribers get nothing. Mirrors
        broadcast_to_team / broadcast_to_region (topic context stamped on the
        message, failed sends pruned via disconnect). ``exclude`` skips one
        user_id."""
        subscribers = self.topic_subscriptions.get(topic)
        if not subscribers:
            return

        # Add topic context to message
        message["topic"] = topic

        disconnect_users = []
        for user_id in list(subscribers):
            if exclude and user_id == exclude:
                continue

            ws = self.active_connections.get(user_id)
            if ws is None:
                disconnect_users.append(user_id)
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error publishing topic '{topic}' to user {user_id}: {e}")
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
    
    async def send_turn_pool_update(self, user_id: str, turn_data: Dict[str, Any]):
        """Push an authoritative turn-pool snapshot to the pool's owner.

        The promised SYSTEMS/turn-regeneration.md "Authoritative push": a
        player-scoped ``turn_pool_updated`` frame emitted by ``regenerate_turns``
        whenever lazy regen actually credits turns (N>0), so connected clients
        refresh the pool without polling. Mirrors send_hostile_detected /
        send_personal_message (typed message, personal scope). ``user_id`` is the
        owning User's id string (the key send_personal_message routes on)."""
        message = {
            "type": "turn_pool_updated",
            "timestamp": datetime.now(UTC).isoformat(),
            **turn_data,
        }
        await self.send_personal_message(user_id, message)

    async def send_hostile_detected(self, owner_user_id: str, payload: Dict[str, Any]):
        """Push a player-scoped ``hostile_detected`` frame to a planet owner.

        A Long-Range Scanner Array (citadel_service DEFENSE_BUILDINGS
        "scanner_array", effect detection_range_sectors) on the owner's planet
        picks up a hostile ship moving within detection range of the planet's
        sector. Mirrors send_turn_pool_update (typed message, personal scope).
        ``owner_user_id`` is the owning User's id string (the key
        send_personal_message routes on); ``payload`` carries
        ``{sector_id, detection_range, ship_id, detected_player_id}``."""
        message = {
            "type": "hostile_detected",
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        }
        await self.send_personal_message(owner_user_id, message)

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
    
    async def send_bounty_update(
        self,
        action: str,
        bounty_data: Dict[str, Any],
        placer_id: str = None,
        target_id: str = None,
    ):
        """Broadcast a bounty lifecycle event (place / collect / cancel).

        Mirrors send_market_update / send_combat_update: a typed ``bounty_updated``
        message. The bounty board is globally interesting (anyone can hunt), so
        the event broadcasts globally; the placer and target additionally get a
        personal copy (already covered by the global broadcast if connected, but
        sent explicitly so they receive it even when the global fan-out is
        filtered). ``action`` is one of "placed" | "collected" | "cancelled".
        """
        message = {
            "type": "bounty_updated",
            "action": action,
            "timestamp": datetime.now(UTC).isoformat(),
            **bounty_data,
        }
        await self.broadcast_global(message)
        if placer_id:
            await self.send_personal_message(placer_id, message)
        if target_id:
            await self.send_personal_message(target_id, message)

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
        
        # If admin already connected, evict the old connection. Close with
        # 4001/reason="superseded" (mirrors connect()'s player-side fix,
        # WO-RT-EVICTION-SUPERSEDE) so the client can tell this apart from an
        # auth rejection and skip its reconnect-with-refresh path
        # (WO-RT-ADMIN-EVICTION). The route's finally block passes its own
        # socket to disconnect_admin(), so the evicted handler can't turn
        # around and scrub this new connection's registration.
        if admin_id in self.admin_connections:
            try:
                await self.admin_connections[admin_id].close(code=4001, reason="superseded")
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
    
    async def disconnect_admin(self, admin_id: str, websocket: Optional[WebSocket] = None):
        """Remove an admin WebSocket connection.

        `websocket`, when passed, must be the exact socket the caller owns
        (the route's finally block passes its own connection object). If a
        newer socket has since replaced it in admin_connections, this is a
        no-op — an evicted handler's finally must never scrub the socket
        that superseded it (WO-RT-ADMIN-EVICTION, mirrors disconnect()'s
        player-side fix in WO-RT-EVICTION-SUPERSEDE). Internal prune paths
        that only know the admin_id (e.g. send-failure cleanup, stale-heartbeat
        sweep) call this with websocket=None and keep the prior unconditional
        behavior.
        """
        if admin_id not in self.admin_connections:
            return
        if websocket is not None and self.admin_connections[admin_id] is not websocket:
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

    async def update_user_region(self, user_id: str, new_region_id: Optional[str]):
        """Move a connected user between region rooms on a region transfer.

        WO-DBB-RT4 — keep region_connections correct when a player crosses a
        region boundary (e.g. through a warp gate into another region) so they
        receive the destination region's governance/election/treaty broadcasts
        and stop receiving the origin's. Mirrors update_user_location for
        sectors: leave the old region set, join the new. ``new_region_id`` may
        be None (player has no region context). No-op for an unknown user."""
        if user_id not in self.connection_metadata:
            return

        metadata = self.connection_metadata[user_id]
        old_region = metadata.get("current_region")
        new_region = str(new_region_id) if new_region_id is not None else None

        if old_region == new_region:
            return

        # Remove from old region
        if old_region and old_region in self.region_connections:
            self.region_connections[old_region].discard(user_id)
            if not self.region_connections[old_region]:
                del self.region_connections[old_region]

        # Add to new region
        if new_region:
            if new_region not in self.region_connections:
                self.region_connections[new_region] = set()
            self.region_connections[new_region].add(user_id)

        # Update metadata
        metadata["current_region"] = new_region

    async def update_user_team(self, user_id: str, new_team_id: Optional[str]):
        """Move a connected user between team rooms on a team-membership change.

        WO-RT-ROOM-HOP — keep team_connections correct when a player joins,
        creates, is kicked from, or leaves a team, so team chat
        (broadcast_to_team) and the revalidation gate in
        handle_websocket_message's "team" chat branch (this file, ~line 921:
        ``if user_id in connection_manager.team_connections.get(team_id,
        set())``) stay correct without requiring a reconnect — a kicked
        member immediately stops receiving AND sending team chat. Mirrors
        update_user_region for regions: leave the old team set, join the new.
        ``new_team_id`` may be None (player left/was removed and has no
        team). No-op for an unknown user."""
        if user_id not in self.connection_metadata:
            return

        metadata = self.connection_metadata[user_id]
        old_team = metadata.get("team_id")
        new_team = str(new_team_id) if new_team_id is not None else None

        if old_team == new_team:
            return

        # Remove from old team
        if old_team and old_team in self.team_connections:
            self.team_connections[old_team].discard(user_id)
            if not self.team_connections[old_team]:
                del self.team_connections[old_team]

        # Add to new team
        if new_team:
            if new_team not in self.team_connections:
                self.team_connections[new_team] = set()
            self.team_connections[new_team].add(user_id)

        # Update metadata
        metadata["team_id"] = new_team

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
            "regions_with_players": len(self.region_connections),
            "active_topics": len(self.topic_subscriptions),
            "connections_by_sector": {
                sector_id: len(users) for sector_id, users in self.sector_connections.items()
            },
            "connections_by_team": {
                team_id: len(users) for team_id, users in self.team_connections.items()
            },
            "connections_by_region": {
                region_id: len(users) for region_id, users in self.region_connections.items()
            },
            "subscribers_by_topic": {
                topic: len(users) for topic, users in self.topic_subscriptions.items()
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
            # Snapshot the user's prior location + name BEFORE disconnect, which
            # deletes connection_metadata[user_id]. We emit presence_updated
            # AFTER the drop using this snapshot (WO-G1).
            stale_meta = self.connection_metadata.get(user_id, {})
            prior_sector = stale_meta.get("current_sector")
            prior_username = stale_meta.get("user_data", {}).get("username")
            ws = self.active_connections.get(user_id)
            if ws:
                try:
                    await ws.close(code=4008, reason="Heartbeat timeout")
                except Exception:
                    pass
            await self.disconnect(user_id)

            # WO-G1: announce that a stale socket was dropped. disconnect()
            # already fans a player_left_sector frame to co-sector peers; this
            # adds an explicit presence_updated event naming the dropped user so
            # presence consumers (co-sector subscribers + admins) converge after
            # the sweep. POST-drop + best-effort: a WS hiccup here must never
            # break the sweep loop, so each emit is isolated in its own try.
            presence_event = {
                "type": "presence_updated",
                "event": "dropped",
                "reason": "heartbeat_timeout",
                "user_id": user_id,
                "username": prior_username,
                "sector_id": prior_sector,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if prior_sector:
                try:
                    await self.broadcast_to_sector(prior_sector, dict(presence_event))
                except Exception as e:
                    logger.warning(
                        f"presence_updated sector emit failed for dropped user {user_id}: {e}"
                    )
            try:
                await self.broadcast_to_admins(dict(presence_event))
            except Exception as e:
                logger.warning(
                    f"presence_updated admin emit failed for dropped user {user_id}: {e}"
                )

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

        elif target_type == "private":
            # WO-RT-BUS-HARDENING: the ephemeral "Private" room from
            # OPERATIONS/realtime.md#3-rooms ("direct player-to-player"),
            # NOT the persistent mailbox (message_service.py / POST
            # /api/v1/messages, already wired to a WS priority-delivery
            # push per FINDINGS.md 2026-06-12). This is live-only chat: if
            # the recipient isn't connected right now, nothing is stored —
            # the sender is pointed at the persistent mailbox instead.
            # Payload key / echo semantics are NO-CANON (no wire-format spec
            # exists for this room yet); kept minimal, flagged to the
            # Orchestrator.
            target_user_id = str(message_data.get("target_user_id") or "").strip()
            if not target_user_id or target_user_id == user_id:
                await connection_manager.send_personal_message(user_id, {
                    "type": "error",
                    "message": "Private chat requires a valid target_user_id"
                })
                return

            if not await _private_chat_recipient_exists(target_user_id):
                await connection_manager.send_personal_message(user_id, {
                    "type": "error",
                    "message": "Recipient not found"
                })
                return

            if target_user_id not in connection_manager.active_connections:
                await connection_manager.send_personal_message(user_id, {
                    "type": "error",
                    "code": "recipient_offline",
                    "message": "Recipient is offline. Send a persistent message via the mailbox instead."
                })
                return

            chat_message["target_user_id"] = target_user_id
            await connection_manager.send_personal_message(target_user_id, chat_message)
            await connection_manager.send_personal_message(user_id, chat_message)

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
    
    elif message_type == "subscribe_topic":
        # WO-DBB-RT5: opt the connection into a generic topic so a later
        # publish_topic() reaches it. Cap topic name length to keep the
        # registry key bounded; the service-side publisher decides what topics
        # carry sensitive data (this is opt-in fan-out, not authorization —
        # publish_topic only ever delivers what a service explicitly fans out).
        topic = (message_data.get("topic") or "").strip()
        if not topic or len(topic) > MAX_TOPIC_NAME_LENGTH:  # NO-CANON: cap to bound the registry key
            await connection_manager.send_personal_message(user_id, {
                "type": "error",
                "message": "Invalid topic name"
            })
            return
        accepted = connection_manager.subscribe_topic(user_id, topic)
        if not accepted:
            # WO-RT-BUS-HARDENING: canon's per-socket subscription cap (50,
            # SYSTEMS/realtime-bus.md Rate limits table) was hit — reject and
            # do not register (realtime-bus.md's "Subscription spam" failure
            # mode names this exact frame type).
            await connection_manager.send_personal_message(user_id, {
                "type": "subscription_rejected",
                "topic": topic,
                "reason": f"Subscription limit reached ({MAX_TOPICS_PER_USER} topics max)",
                "current_count": connection_manager.count_topic_subscriptions(user_id),
                "timestamp": datetime.now(UTC).isoformat()
            })
            return
        await connection_manager.send_personal_message(user_id, {
            "type": "topic_subscribed",
            "topic": topic,
            "timestamp": datetime.now(UTC).isoformat()
        })

    elif message_type == "unsubscribe_topic":
        # WO-DBB-RT5: opt out of a generic topic.
        topic = (message_data.get("topic") or "").strip()
        if topic:
            connection_manager.unsubscribe_topic(user_id, topic)
        await connection_manager.send_personal_message(user_id, {
            "type": "topic_unsubscribed",
            "topic": topic,
            "timestamp": datetime.now(UTC).isoformat()
        })

    elif message_type == "aria_chat":
        await handle_aria_chat(user_id, message_data)

    else:
        logger.warning(f"Unknown WebSocket message type: {message_type} from user {user_id}")


async def _private_chat_recipient_exists(target_user_id: str) -> bool:
    """Check that target_user_id names a real, non-deleted User row.

    WO-RT-BUS-HARDENING: private chat needs to tell "no such user" apart from
    "real user, just not connected right now" (the latter steers the sender
    toward the persistent mailbox instead). This handler is otherwise DB-free,
    operating only on the in-memory ConnectionManager registries; opens its
    own short-lived session the same way handle_aria_chat does below."""
    import uuid as _uuid

    try:
        target_uuid = _uuid.UUID(str(target_user_id))
    except (ValueError, TypeError, AttributeError):
        return False

    try:
        from sqlalchemy import select

        from src.core.database import AsyncSessionLocal
        from src.models.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User.id).where(User.id == target_uuid, User.deleted.is_(False))
            )
            return result.scalar_one_or_none() is not None
    except Exception as e:
        logger.error(f"Private-chat recipient lookup failed for {target_user_id}: {e}")
        return False


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
            # Let the service build the ConversationContext: only it has the
            # authenticated assistant id, and ConversationContext validation
            # rejects an empty assistant_id (the old pre-built context with
            # assistant_id="" raised "Validation failed for id" on every
            # threaded follow-up query). Pass the client's conversation_id so
            # the service can continue the thread when it is a valid id.
            result = await ai_service.process_natural_language_query(
                player_id=_uuid.UUID(str(player_id)),
                user_input=content,
                conversation_id=conversation_id,
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