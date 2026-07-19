"""
Redis Pub/Sub Service for Real-Time Market Data Broadcasting
Enables efficient broadcasting of market updates to multiple WebSocket clients

This service implements:
- Market update publishing to Redis channels
- Subscriber management for commodities
- Performance optimization for 1000+ concurrent users
- OWASP-compliant message validation
"""

import json
import asyncio
import logging
import uuid
from typing import Dict, Set, List, Any, Optional, Callable
from datetime import datetime, UTC
from dataclasses import dataclass
from collections import defaultdict

import redis.asyncio as redis
from redis.asyncio.client import PubSub

from src.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChannelSubscription:
    """Track subscription details for a channel"""
    channel_name: str
    player_ids: Set[str]
    created_at: datetime
    last_activity: datetime
    message_count: int = 0


class RedisPubSubService:
    """
    Redis Pub/Sub Service for real-time data broadcasting
    Optimized for high-frequency market updates
    """
    
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[PubSub] = None
        
        # Track active subscriptions
        self.channel_subscriptions: Dict[str, ChannelSubscription] = {}
        self.player_channels: Dict[str, Set[str]] = defaultdict(set)  # player_id -> channels
        
        # Performance tracking
        self.messages_published = 0
        self.messages_received = 0
        self.active_listeners = 0
        
        # Channel patterns
        self.MARKET_CHANNEL_PREFIX = "market:"
        self.TRADING_CHANNEL_PREFIX = "trading:"
        self.AI_CHANNEL_PREFIX = "ai:"
        self.SYSTEM_CHANNEL = "system:broadcast"

        # WO-P1-REALTIME-BUS-FANOUT (canon SYSTEMS/realtime-bus.md "Cross-
        # process fanout" -- this is the doc's own literal channel name).
        # personal/sector WS delivery crosses uvicorn workers through this
        # single shared channel, distinct from the per-commodity market
        # channels above. worker_id tags every publish so a worker's own
        # bus subscriber can recognize (and skip) its own just-published
        # event -- it already delivered to its local sockets, synchronously,
        # before publishing; see ConnectionManager._handle_bus_envelope in
        # websocket_service.py.
        self.BUS_CHANNEL = "sw2102:bus"
        self.worker_id = uuid.uuid4().hex

        logger.info(f"Redis Pub/Sub Service initialized with URL: {self.redis_url}")
    
    async def connect(self):
        """Initialize Redis connection and pub/sub client"""
        try:
            self.redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50  # Support many concurrent connections
            )
            
            # Test connection
            await self.redis_client.ping()
            
            # Create pub/sub client
            self.pubsub = self.redis_client.pubsub()
            
            logger.info("Redis Pub/Sub connection established")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return False
    
    async def disconnect(self):
        """Clean up Redis connections"""
        try:
            if self.pubsub:
                await self.pubsub.unsubscribe()
                await self.pubsub.close()
            
            if self.redis_client:
                await self.redis_client.close()
            
            logger.info("Redis Pub/Sub disconnected")
            
        except Exception as e:
            logger.error(f"Error disconnecting from Redis: {e}")
    
    # =============================================================================
    # PUBLISHING
    # =============================================================================
    
    async def publish_market_update(self, commodity: str, market_data: Dict[str, Any]):
        """
        Publish market update to commodity-specific channel
        All subscribers to this commodity will receive the update
        """
        channel = f"{self.MARKET_CHANNEL_PREFIX}{commodity}"
        
        try:
            message = {
                "type": "market_update",
                "commodity": commodity,
                "data": market_data,
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            # Publish to Redis
            subscribers = await self.redis_client.publish(
                channel,
                json.dumps(message)
            )
            
            self.messages_published += 1
            
            # Update subscription tracking
            if channel in self.channel_subscriptions:
                sub = self.channel_subscriptions[channel]
                sub.last_activity = datetime.now(UTC)
                sub.message_count += 1
            
            logger.debug(f"Published market update for {commodity} to {subscribers} subscribers")
            return subscribers
            
        except Exception as e:
            logger.error(f"Error publishing market update: {e}")
            return 0
    
    async def publish_trading_event(self, event_type: str, event_data: Dict[str, Any]):
        """
        Publish trading event (trade executed, order placed, etc.)
        These are broadcast to all interested parties
        """
        channel = f"{self.TRADING_CHANNEL_PREFIX}{event_type}"
        
        try:
            message = {
                "type": "trading_event",
                "event_type": event_type,
                "data": event_data,
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            subscribers = await self.redis_client.publish(
                channel,
                json.dumps(message)
            )
            
            self.messages_published += 1
            
            logger.debug(f"Published trading event {event_type} to {subscribers} subscribers")
            return subscribers
            
        except Exception as e:
            logger.error(f"Error publishing trading event: {e}")
            return 0
    
    async def publish_ai_signal(self, signal_type: str, signal_data: Dict[str, Any]):
        """
        Publish AI trading signals and predictions
        """
        channel = f"{self.AI_CHANNEL_PREFIX}{signal_type}"
        
        try:
            message = {
                "type": "ai_signal",
                "signal_type": signal_type,
                "data": signal_data,
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            subscribers = await self.redis_client.publish(
                channel,
                json.dumps(message)
            )
            
            self.messages_published += 1
            
            logger.debug(f"Published AI signal {signal_type} to {subscribers} subscribers")
            return subscribers
            
        except Exception as e:
            logger.error(f"Error publishing AI signal: {e}")
            return 0
    
    async def broadcast_system_message(self, message: str, priority: str = "info"):
        """
        Broadcast system-wide messages (maintenance, announcements, etc.)
        """
        try:
            data = {
                "type": "system_message",
                "message": message,
                "priority": priority,
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            subscribers = await self.redis_client.publish(
                self.SYSTEM_CHANNEL,
                json.dumps(data)
            )
            
            logger.info(f"Broadcast system message to {subscribers} subscribers: {message}")
            return subscribers
            
        except Exception as e:
            logger.error(f"Error broadcasting system message: {e}")
            return 0

    async def publish_bus_event(
        self,
        kind: str,
        target: Any,
        message: Dict[str, Any],
        exclude_user: Optional[str] = None,
    ) -> int:
        """Publish a personal/sector WS event to the shared cross-worker bus
        channel (WO-P1-REALTIME-BUS-FANOUT, canon SYSTEMS/realtime-bus.md
        "Cross-process fanout"). ``kind`` is "personal" or "sector";
        ``target`` is the user_id or sector_id the ORIGINATING
        ConnectionManager already tried (or, for sector, always attempts)
        to serve locally before calling this. Every worker subscribed to
        BUS_CHANNEL receives the envelope, including the publisher itself
        -- ConnectionManager._handle_bus_envelope skips envelopes whose
        origin_worker_id matches its own (already delivered locally) and
        otherwise attempts local-only delivery for whichever of its own
        connections match.

        Best-effort by design, mirroring publish_market_update /
        publish_trading_event's own except-log-return-0 shape: a Redis
        outage must degrade cross-worker fanout, never break the caller
        (which has ALREADY completed its own local delivery attempt by the
        time this runs -- realtime-bus.md invariant 4, "every send is
        non-blocking with respect to the originating service")."""
        try:
            envelope = {
                "kind": kind,
                "target": target,
                "message": message,
                "exclude_user": exclude_user,
                "origin_worker_id": self.worker_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            subscribers = await self.redis_client.publish(
                self.BUS_CHANNEL, json.dumps(envelope)
            )
            self.messages_published += 1
            return subscribers
        except Exception as e:
            logger.error(f"Error publishing bus event (kind={kind}): {e}")
            return 0

    async def subscribe_bus(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        """Persistent cross-worker bus listener (WO-P1-REALTIME-BUS-FANOUT).

        Unlike subscribe_to_market_updates (one ephemeral subscription per
        client-requested commodity list, torn down when that client
        disconnects), this is a SINGLE per-process subscription meant to
        run for the lifetime of the worker -- started once, lazily, from
        ConnectionManager.connect() (see websocket_service.py; every
        connection route funnels through it, so this covers both the
        plain player route and the enhanced/Foundation-Sprint route).

        Resilience (team caution): a malformed message or a callback
        exception logs-and-continues -- never lets a single bad frame kill
        the loop, since this is the only channel remote workers have."""
        subscriber = self.redis_client.pubsub()
        try:
            await subscriber.subscribe(self.BUS_CHANNEL)
            self.active_listeners += 1

            async for message in subscriber.listen():
                if message.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.error(f"Invalid JSON on bus channel: {message.get('data')!r}")
                    continue
                try:
                    await callback(envelope)
                    self.messages_received += 1
                except Exception:
                    logger.exception("Error in bus subscriber callback")
        finally:
            try:
                await subscriber.unsubscribe()
                await subscriber.close()
            except Exception:
                pass
            self.active_listeners -= 1

    # =============================================================================
    # SUBSCRIBING
    # =============================================================================
    
    async def subscribe_to_market_updates(self, commodities: List[str], 
                                        callback: Callable[[Dict[str, Any]], None],
                                        player_id: str = None):
        """
        Subscribe to market updates for specific commodities
        Callback will be called with each update
        """
        channels = [f"{self.MARKET_CHANNEL_PREFIX}{commodity}" for commodity in commodities]
        
        try:
            # Create new pubsub instance for this subscription
            subscriber = self.redis_client.pubsub()
            await subscriber.subscribe(*channels)
            
            # Track subscriptions
            for channel in channels:
                if channel not in self.channel_subscriptions:
                    self.channel_subscriptions[channel] = ChannelSubscription(
                        channel_name=channel,
                        player_ids=set(),
                        created_at=datetime.now(UTC),
                        last_activity=datetime.now(UTC)
                    )
                
                if player_id:
                    self.channel_subscriptions[channel].player_ids.add(player_id)
                    self.player_channels[player_id].update(channels)
            
            self.active_listeners += 1
            
            # Listen for messages
            async for message in subscriber.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await callback(data)
                        self.messages_received += 1
                    except json.JSONDecodeError:
                        logger.error(f"Invalid JSON in message: {message['data']}")
                    except Exception as e:
                        logger.error(f"Error in subscription callback: {e}")
            
        except Exception as e:
            logger.error(f"Error in market subscription: {e}")
        finally:
            await subscriber.unsubscribe()
            await subscriber.close()
            self.active_listeners -= 1
    
    async def unsubscribe_player(self, player_id: str):
        """
        Remove player from all channel subscriptions
        Called when player disconnects
        """
        channels = self.player_channels.get(player_id, set())
        
        for channel in channels:
            if channel in self.channel_subscriptions:
                self.channel_subscriptions[channel].player_ids.discard(player_id)
                
                # Remove empty subscriptions
                if not self.channel_subscriptions[channel].player_ids:
                    del self.channel_subscriptions[channel]
        
        # Clear player's channel list
        if player_id in self.player_channels:
            del self.player_channels[player_id]
        
        logger.info(f"Unsubscribed player {player_id} from all channels")
    
    # =============================================================================
    # MONITORING
    # =============================================================================
    
    def get_subscription_stats(self) -> Dict[str, Any]:
        """Get current subscription statistics"""
        commodity_subscribers = {}
        
        for channel, sub in self.channel_subscriptions.items():
            if channel.startswith(self.MARKET_CHANNEL_PREFIX):
                commodity = channel.replace(self.MARKET_CHANNEL_PREFIX, "")
                commodity_subscribers[commodity] = len(sub.player_ids)
        
        return {
            "total_channels": len(self.channel_subscriptions),
            "active_listeners": self.active_listeners,
            "messages_published": self.messages_published,
            "messages_received": self.messages_received,
            "commodity_subscribers": commodity_subscribers,
            "total_players": len(self.player_channels)
        }
    
    async def health_check(self) -> bool:
        """Check Redis connection health"""
        try:
            if self.redis_client:
                await self.redis_client.ping()
                return True
            return False
        except:
            return False


# Singleton instance
_pubsub_service: Optional["RedisPubSubService"] = None

# WO-P1-BUS-RACE-CRITICAL: single-flight guard for the lazy singleton.
# asyncio.Lock() is safe to construct at module import time on Python 3.10+
# (it no longer binds to a specific event loop at construction), so this is
# a plain module-level lock, not something built lazily per-loop.
_pubsub_service_lock = asyncio.Lock()


async def get_pubsub_service() -> RedisPubSubService:
    """Get or create the pub/sub service instance -- single-flight safe.

    BUG THIS FIXES (Mack's empirical repro, probe_bus_race.py): the old
    body did `if _pubsub_service is None: _pubsub_service =
    RedisPubSubService(); await _pubsub_service.connect()` -- the global
    was assigned BEFORE the connect() await suspended, so a second
    concurrent caller arriving while the first's connect() was still in
    flight saw a non-None global and returned the SAME instance with
    `.redis_client` still None, silently. In
    ConnectionManager._start_bus_subscriber that meant the bus subscriber
    concluded "Redis unavailable" and gave up FOREVER for that worker's
    process lifetime -- a race, not a real outage, mistaken for a
    permanent one, exactly the situation the live 2-worker deploy window
    was about to hit for the first time (Mac/CI have no Redis at all, so
    both racing callers agreed "unavailable" and nothing ever caught it).

    Fixed via double-checked locking: the fast path (already initialized)
    never touches the lock; the slow path acquires `_pubsub_service_lock`
    and re-checks under it, so at most ONE caller ever constructs +
    connects the singleton, and every other concurrent caller AWAITS that
    same in-flight attempt (blocked on the lock) rather than racing it.
    The module-level global is assigned ONLY after connect() has fully
    completed -- never before -- so no caller can ever observe a non-None
    singleton whose connect() hasn't finished.
    """
    global _pubsub_service
    if _pubsub_service is not None:
        return _pubsub_service
    async with _pubsub_service_lock:
        if _pubsub_service is None:
            service = RedisPubSubService()
            await service.connect()
            _pubsub_service = service
        return _pubsub_service