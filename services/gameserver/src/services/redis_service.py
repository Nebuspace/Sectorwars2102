"""
Redis Service for Real-time Messaging and Caching
Provides hybrid PostgreSQL + Redis architecture for optimal performance
"""

import json
import asyncio
from typing import Any, Optional, Dict, List
from datetime import datetime, timedelta
# aioredis is archived upstream and dead on Python 3.12 (its import chain
# hits `distutils`, removed in 3.12); redis-py >=4.2 absorbed the aioredis
# 2.x codebase as `redis.asyncio`, so this is a drop-in swap — no new
# dependency, `redis` with the `hiredis` extra is already in pyproject.toml.
from redis import asyncio as aioredis
import redis
from src.core.config import settings


class RedisService:
    """
    Redis service for real-time messaging, session management, and caching
    
    This service complements PostgreSQL by handling:
    - Real-time player movement notifications
    - Cross-regional messaging and events  
    - Session data and temporary state
    - Live gameplay event broadcasting
    - Service discovery for regional containers
    """
    
    def __init__(self):
        self.redis_pool: Optional[aioredis.Redis] = None
        self.sync_redis: Optional[redis.Redis] = None
        self.pubsub = None
        
    async def connect(self):
        """Initialize Redis connection pool"""
        try:
            self.redis_pool = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20
            )
            
            # Test connection
            await self.redis_pool.ping()
            print(f"✅ Redis connected successfully at {settings.REDIS_URL}")
            
            # Initialize sync client for non-async operations
            self.sync_redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
            raise
    
    async def disconnect(self):
        """Close Redis connections"""
        if self.redis_pool:
            await self.redis_pool.close()
        if self.sync_redis:
            self.sync_redis.close()
    
    # ================================
    # REAL-TIME MESSAGING
    # ================================
    
    async def publish_player_movement(self, player_id: str, sector_id: str, ship_data: Dict):
        """Broadcast player movement to other players in the same sector"""
        message = {
            "type": "player_movement",
            "player_id": player_id,
            "sector_id": sector_id,
            "ship_data": ship_data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        channel = f"sector:{sector_id}:movement"
        await self.redis_pool.publish(channel, json.dumps(message))
    
    async def publish_trade_event(self, trade_data: Dict):
        """Broadcast trade events for market intelligence"""
        message = {
            "type": "trade_event",
            "data": trade_data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Publish to both sector and regional channels
        sector_channel = f"sector:{trade_data['sector_id']}:trades"
        regional_channel = f"region:{trade_data.get('region_id', 'central-nexus')}:trades"
        
        await asyncio.gather(
            self.redis_pool.publish(sector_channel, json.dumps(message)),
            self.redis_pool.publish(regional_channel, json.dumps(message))
        )
    
    async def publish_combat_event(self, combat_data: Dict):
        """Broadcast combat events for real-time updates"""
        message = {
            "type": "combat_event",
            "data": combat_data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        channel = f"sector:{combat_data['sector_id']}:combat"
        await self.redis_pool.publish(channel, json.dumps(message))
    
    async def subscribe_to_sector_events(self, sector_id: str):
        """Subscribe to all events in a specific sector"""
        channels = [
            f"sector:{sector_id}:movement",
            f"sector:{sector_id}:trades", 
            f"sector:{sector_id}:combat"
        ]
        
        pubsub = self.redis_pool.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub
    
    # ================================
    # SESSION MANAGEMENT
    # ================================
    
    async def store_session(self, session_id: str, user_id: str, session_data: Dict):
        """Store player session data"""
        key = f"session:{session_id}"
        data = {
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "last_activity": datetime.utcnow().isoformat(),
            **session_data
        }
        
        await self.redis_pool.setex(
            key, 
            settings.REDIS_SESSION_TTL, 
            json.dumps(data)
        )
    
    async def get_session(self, session_id: str) -> Optional[Dict]:
        """Retrieve session data"""
        key = f"session:{session_id}"
        data = await self.redis_pool.get(key)
        
        if data:
            session = json.loads(data)
            # Update last activity
            session["last_activity"] = datetime.utcnow().isoformat()
            await self.redis_pool.setex(
                key, 
                settings.REDIS_SESSION_TTL, 
                json.dumps(session)
            )
            return session
        
        return None
    
    async def invalidate_session(self, session_id: str):
        """Remove session data"""
        key = f"session:{session_id}"
        await self.redis_pool.delete(key)
    
    # ================================
    # CACHING
    # ================================
    
    async def cache_set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Store data in cache with optional TTL"""
        ttl = ttl or settings.REDIS_CACHE_TTL
        serialized_value = json.dumps(value) if not isinstance(value, str) else value
        
        await self.redis_pool.setex(key, ttl, serialized_value)
    
    async def cache_get(self, key: str) -> Optional[Any]:
        """Retrieve data from cache"""
        value = await self.redis_pool.get(key)
        
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        
        return None
    
    async def cache_delete(self, key: str):
        """Remove data from cache"""
        await self.redis_pool.delete(key)
    
    async def cache_market_data(self, sector_id: str, market_data: Dict):
        """Cache market data for quick access"""
        key = f"market:sector:{sector_id}"
        await self.cache_set(key, market_data, ttl=300)  # 5 minute cache
    
    async def get_cached_market_data(self, sector_id: str) -> Optional[Dict]:
        """Get cached market data"""
        key = f"market:sector:{sector_id}"
        return await self.cache_get(key)
    
    # ================================
    # CROSS-REGIONAL COORDINATION
    # ================================
    
    async def register_regional_service(self, region_id: str, service_info: Dict):
        """Register a regional service for discovery"""
        key = f"service_registry:region:{region_id}"
        await self.cache_set(key, service_info, ttl=3600)  # 1 hour registration
    
    async def discover_regional_services(self) -> Dict[str, Dict]:
        """Discover all registered regional services"""
        pattern = "service_registry:region:*"
        keys = await self.redis_pool.keys(pattern)
        
        services = {}
        for key in keys:
            region_id = key.split(":")[-1]
            service_info = await self.cache_get(key)
            if service_info:
                services[region_id] = service_info
        
        return services
    
    async def notify_cross_region_travel(self, player_id: str, from_region: str, to_region: str):
        """Notify regions about player travel"""
        message = {
            "type": "cross_region_travel",
            "player_id": player_id,
            "from_region": from_region,
            "to_region": to_region,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Notify both source and destination regions
        await asyncio.gather(
            self.redis_pool.publish(f"region:{from_region}:travel", json.dumps(message)),
            self.redis_pool.publish(f"region:{to_region}:travel", json.dumps(message))
        )
    
    # ================================
    # GAME STATE SYNCHRONIZATION
    # ================================
    
    async def sync_player_online_status(self, player_id: str, is_online: bool):
        """Track player online status"""
        key = f"player_online:{player_id}"
        
        if is_online:
            await self.redis_pool.setex(key, 300, "online")  # 5 minute heartbeat
        else:
            await self.redis_pool.delete(key)
    
    async def get_online_players_in_sector(self, sector_id: str) -> List[str]:
        """Get list of online players in a sector"""
        # This would need to be combined with PostgreSQL sector data
        # For now, return players who have recent activity
        pattern = "player_online:*"
        keys = await self.redis_pool.keys(pattern)
        
        online_players = []
        for key in keys:
            player_id = key.split(":")[-1]
            # Here we'd check if player is actually in this sector via PostgreSQL
            # For demonstration, adding all online players
            online_players.append(player_id)
        
        return online_players
    
    # ================================
    # UTILITY METHODS
    # ================================
    
    async def health_check(self) -> Dict[str, Any]:
        """Check Redis health and return status"""
        try:
            start_time = datetime.utcnow()
            await self.redis_pool.ping()
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            info = await self.redis_pool.info()
            
            return {
                "status": "healthy",
                "response_time_ms": response_time,
                "connected_clients": info.get("connected_clients", 0),
                "used_memory": info.get("used_memory_human", "unknown"),
                "redis_version": info.get("redis_version", "unknown")
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }


# Global Redis service instance
redis_service = RedisService()


async def get_redis_service() -> RedisService:
    """Get the Redis service instance (dependency injection)"""
    return redis_service


async def init_redis():
    """Initialize Redis service on application startup"""
    await redis_service.connect()


async def close_redis():
    """Close Redis connections on application shutdown"""
    await redis_service.disconnect()