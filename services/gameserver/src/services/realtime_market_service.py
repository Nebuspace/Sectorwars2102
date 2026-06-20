"""
Real-Time Market Data Service for Backend Integration
Provides live market data from database with caching and performance optimization

This service bridges the gap between Foundation Sprint's real-time requirements
and the game's database, providing:
- Live price updates from actual transactions
- AI prediction integration
- Efficient caching with Redis
- Performance optimization for 1000+ concurrent users
"""

import asyncio
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from dataclasses import dataclass, asdict
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, text
from sqlalchemy.orm import selectinload
import redis.asyncio as redis

from src.models.market_transaction import MarketTransaction
from src.models.ai_trading import AIMarketPrediction, PlayerTradingProfile
from src.models.station import Station
from src.models.sector import Sector
from src.models.player import Player
from src.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Real-time market data snapshot for a commodity"""
    commodity: str
    current_price: float
    volume_24h: int
    high_24h: float
    low_24h: float
    price_change_24h: float
    price_change_percent: float
    last_transaction: datetime
    bid_ask_spread: float
    # No real order-book mechanic exists; always emitted empty {"bids": [], "asks": []}.
    # Retained only because enhanced_websocket_service reads this field.
    market_depth: Dict[str, List[Tuple[float, int]]]
    sector_prices: Dict[int, float]  # sector_id -> local price
    ai_prediction: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "commodity": self.commodity,
            "current_price": self.current_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "price_change_24h": self.price_change_24h,
            "price_change_percent": self.price_change_percent,
            "last_transaction": self.last_transaction.isoformat(),
            "bid_ask_spread": self.bid_ask_spread,
            "market_depth": {
                "bids": self.market_depth.get("bids", []),
                "asks": self.market_depth.get("asks", [])
            },
            "sector_prices": self.sector_prices,
            "ai_prediction": self.ai_prediction
        }


@dataclass
class TradingSignal:
    """Real-time trading signal from market analysis"""
    signal_type: str  # "buy", "sell", "hold", "alert"
    commodity: str
    strength: float  # 0.0 to 1.0
    reason: str
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    confidence: float = 0.5
    expires_at: Optional[datetime] = None


class RealTimeMarketService:
    """
    Service for providing real-time market data from database
    Optimized for high-frequency updates and concurrent users
    """
    
    def __init__(self, redis_client: redis.Redis = None):
        self.redis = redis_client
        self.cache_ttl = 1  # 1 second cache for real-time feel
        self.market_update_interval = 1  # Update every second
        
        # Performance tracking
        self.query_times: List[float] = []
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Commodity configuration (aligned with actual codebase implementation)
        # Core Commodities (7) - using lowercase_underscore convention
        self.valid_commodities = [
            "ore", "organics", "gourmet_food", "fuel",
            "equipment", "exotic_technology", "luxury_goods"
        ]
        # Strategic Resources (not typically traded on open market)
        self.strategic_resources = [
            "colonists", "quantum_shards", "quantum_crystals", "combat_drones"
        ]
        # Rare Materials
        self.rare_materials = [
            "prismatic_ore", "photonic_crystals"
        ]
        
        # Market analysis thresholds
        self.volatility_threshold = 0.1  # 10% price change triggers alert
        self.volume_spike_threshold = 2.0  # 2x average volume
        
        logger.info("Real-Time Market Service initialized")
    
    # =============================================================================
    # MARKET DATA RETRIEVAL
    # =============================================================================
    
    async def get_market_snapshot(self, commodity: str, db: AsyncSession) -> MarketSnapshot:
        """
        Get comprehensive market snapshot for a commodity
        Includes current price, volume, trends, and AI predictions
        """
        start_time = asyncio.get_event_loop().time()
        
        # Try cache first
        cached = await self._get_cached_snapshot(commodity)
        if cached:
            self.cache_hits += 1
            return cached
        
        self.cache_misses += 1
        
        try:
            # Get recent transactions (last 24 hours)
            cutoff_time = datetime.now(UTC) - timedelta(hours=24)
            stmt = select(MarketTransaction).where(
                and_(
                    MarketTransaction.commodity == commodity,
                    MarketTransaction.timestamp > cutoff_time
                )
            ).order_by(MarketTransaction.timestamp.desc()).limit(1000)
            
            result = await db.execute(stmt)
            transactions = result.scalars().all()
            
            if not transactions:
                # No recent transactions, return default snapshot
                return self._create_default_snapshot(commodity)
            
            # Calculate metrics
            current_price = float(transactions[0].price)
            volume_24h = sum(t.quantity for t in transactions)
            high_24h = max(float(t.price) for t in transactions)
            low_24h = min(float(t.price) for t in transactions)
            
            # Price change calculation
            oldest_price = float(transactions[-1].price)
            price_change_24h = current_price - oldest_price
            price_change_percent = (price_change_24h / oldest_price * 100) if oldest_price > 0 else 0
            
            # Market depth: there is NO real order-book mechanic in the game, so
            # we no longer fabricate a synthetic bid/ask book. Emit an empty book.
            # (The field is retained because enhanced_websocket_service reads it;
            #  it now carries real-but-empty data instead of mock orders.)
            market_depth: Dict[str, List[Tuple[float, int]]] = {"bids": [], "asks": []}

            # Sector-specific prices
            sector_prices = await self._get_sector_prices(commodity, db)
            
            # AI prediction
            ai_prediction = await self._get_ai_prediction(commodity, db)
            
            # Bid-ask spread (simplified)
            bid_ask_spread = (high_24h - low_24h) / current_price * 100 if current_price > 0 else 0
            
            snapshot = MarketSnapshot(
                commodity=commodity,
                current_price=current_price,
                volume_24h=volume_24h,
                high_24h=high_24h,
                low_24h=low_24h,
                price_change_24h=price_change_24h,
                price_change_percent=price_change_percent,
                last_transaction=transactions[0].timestamp,
                bid_ask_spread=bid_ask_spread,
                market_depth=market_depth,
                sector_prices=sector_prices,
                ai_prediction=ai_prediction
            )
            
            # Cache the snapshot
            await self._cache_snapshot(commodity, snapshot)
            
            # Track performance
            query_time = asyncio.get_event_loop().time() - start_time
            self.query_times.append(query_time)
            if len(self.query_times) > 100:
                self.query_times = self.query_times[-100:]  # Keep last 100
            
            return snapshot
            
        except Exception as e:
            logger.error(f"Error getting market snapshot for {commodity}: {e}")
            return self._create_default_snapshot(commodity)
    
    async def get_multi_commodity_data(self, commodities: List[str], db: AsyncSession) -> Dict[str, MarketSnapshot]:
        """
        Get market data for multiple commodities efficiently
        Uses parallel queries for performance
        """
        # Filter valid commodities
        valid_commodities = [c for c in commodities if c in self.valid_commodities]
        if not valid_commodities:
            return {}
        
        # Parallel fetch
        tasks = [self.get_market_snapshot(commodity, db) for commodity in valid_commodities]
        snapshots = await asyncio.gather(*tasks)
        
        return {
            commodity: snapshot 
            for commodity, snapshot in zip(valid_commodities, snapshots)
        }
    
    async def _get_sector_prices(self, commodity: str, db: AsyncSession) -> Dict[int, float]:
        """
        Get commodity prices by sector
        Shows regional price variations
        """
        # Get recent transactions grouped by sector
        stmt = text("""
            SELECT 
                s.id as sector_id,
                AVG(mt.price) as avg_price,
                COUNT(*) as transaction_count
            FROM market_transactions mt
            JOIN ports p ON mt.station_id = p.id
            JOIN sectors s ON p.sector_id = s.id
            WHERE 
                mt.commodity = :commodity
                AND mt.timestamp > :cutoff_time
            GROUP BY s.id
            HAVING COUNT(*) >= 5
            ORDER BY transaction_count DESC
            LIMIT 20
        """)
        
        result = await db.execute(
            stmt,
            {
                "commodity": commodity,
                "cutoff_time": datetime.now(UTC) - timedelta(hours=24)
            }
        )
        
        sector_prices = {}
        for row in result:
            sector_prices[row.sector_id] = round(float(row.avg_price), 2)
        
        return sector_prices
    
    async def _get_ai_prediction(self, commodity: str, db: AsyncSession) -> Optional[Dict[str, Any]]:
        """
        Get latest AI prediction for commodity
        """
        try:
            stmt = select(AIMarketPrediction).where(
                and_(
                    AIMarketPrediction.commodity == commodity,
                    AIMarketPrediction.timestamp > datetime.now(UTC) - timedelta(hours=1)
                )
            ).order_by(AIMarketPrediction.timestamp.desc()).limit(1)
            
            result = await db.execute(stmt)
            prediction = result.scalar_one_or_none()
            
            if prediction:
                return {
                    "predicted_price": float(prediction.predicted_price),
                    "confidence": float(prediction.confidence),
                    "trend": prediction.trend,
                    "prediction_time": prediction.timestamp.isoformat(),
                    "factors": prediction.factors or {}
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting AI prediction: {e}")
            return None
    
    # =============================================================================
    # TRADING SIGNALS & ALERTS
    # =============================================================================
    
    async def generate_trading_signals(self, commodity: str, snapshot: MarketSnapshot, 
                                     player_profile: Optional[PlayerTradingProfile] = None) -> List[TradingSignal]:
        """
        Generate trading signals based on market conditions and player profile
        """
        signals = []
        
        # Price volatility signal
        if abs(snapshot.price_change_percent) > self.volatility_threshold * 100:
            signal_type = "sell" if snapshot.price_change_percent > 0 else "buy"
            signals.append(TradingSignal(
                signal_type=signal_type,
                commodity=commodity,
                strength=min(abs(snapshot.price_change_percent) / 20, 1.0),
                reason=f"High volatility: {snapshot.price_change_percent:.1f}% change",
                target_price=snapshot.current_price * (0.95 if signal_type == "buy" else 1.05),
                confidence=0.7
            ))
        
        # AI prediction signal
        if snapshot.ai_prediction:
            predicted_price = snapshot.ai_prediction["predicted_price"]
            price_diff_percent = (predicted_price - snapshot.current_price) / snapshot.current_price * 100
            
            if abs(price_diff_percent) > 5:  # 5% difference threshold
                signal_type = "buy" if price_diff_percent > 0 else "sell"
                signals.append(TradingSignal(
                    signal_type=signal_type,
                    commodity=commodity,
                    strength=min(abs(price_diff_percent) / 10, 1.0),
                    reason=f"AI predicts {price_diff_percent:.1f}% price change",
                    target_price=predicted_price,
                    confidence=snapshot.ai_prediction["confidence"]
                ))
        
        # Volume spike signal
        # (Would need historical average volume for accurate calculation)
        if snapshot.volume_24h > 10000:  # Placeholder threshold
            signals.append(TradingSignal(
                signal_type="alert",
                commodity=commodity,
                strength=0.5,
                reason=f"High trading volume: {snapshot.volume_24h:,} units",
                confidence=0.6
            ))
        
        # Spread opportunity
        if snapshot.bid_ask_spread > 5:  # 5% spread
            signals.append(TradingSignal(
                signal_type="alert",
                commodity=commodity,
                strength=min(snapshot.bid_ask_spread / 10, 1.0),
                reason=f"Wide bid-ask spread: {snapshot.bid_ask_spread:.1f}%",
                confidence=0.8
            ))
        
        # Player profile-based signals
        if player_profile:
            # Add personalized signals based on player's trading patterns
            # This would analyze the player's historical success with this commodity
            pass
        
        return signals
    
    # =============================================================================
    # REAL-TIME STREAMING
    # =============================================================================
    
    async def stream_market_updates(self, commodities: List[str], db: AsyncSession, 
                                  callback: callable, stop_event: asyncio.Event):
        """
        Stream real-time market updates for specified commodities
        Calls callback function with updates
        """
        logger.info(f"Starting market stream for commodities: {commodities}")
        
        last_snapshots = {}
        
        try:
            while not stop_event.is_set():
                # Get current snapshots
                current_snapshots = await self.get_multi_commodity_data(commodities, db)
                
                # Detect changes and send updates
                updates = {}
                for commodity, snapshot in current_snapshots.items():
                    last_snapshot = last_snapshots.get(commodity)
                    
                    # Send update if price changed or first update
                    if not last_snapshot or last_snapshot.current_price != snapshot.current_price:
                        updates[commodity] = snapshot
                        
                        # Generate trading signals
                        signals = await self.generate_trading_signals(commodity, snapshot)
                        if signals:
                            updates[commodity].signals = [asdict(s) for s in signals]
                
                # Send updates if any
                if updates:
                    await callback({
                        "type": "market_update",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "updates": {k: v.to_dict() for k, v in updates.items()}
                    })
                
                # Update last snapshots
                last_snapshots = current_snapshots
                
                # Wait before next update
                await asyncio.sleep(self.market_update_interval)
                
        except Exception as e:
            logger.error(f"Error in market stream: {e}")
            raise
        finally:
            logger.info("Market stream stopped")
    
    # =============================================================================
    # CACHING
    # =============================================================================
    
    async def _get_cached_snapshot(self, commodity: str) -> Optional[MarketSnapshot]:
        """Get cached market snapshot from Redis"""
        if not self.redis:
            return None
        
        try:
            key = f"market:snapshot:{commodity}"
            data = await self.redis.get(key)
            
            if data:
                snapshot_dict = json.loads(data)
                # Reconstruct MarketSnapshot
                snapshot_dict["last_transaction"] = datetime.fromisoformat(snapshot_dict["last_transaction"])
                return MarketSnapshot(**snapshot_dict)
            
            return None
            
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
            return None
    
    async def _cache_snapshot(self, commodity: str, snapshot: MarketSnapshot):
        """Cache market snapshot in Redis"""
        if not self.redis:
            return
        
        try:
            key = f"market:snapshot:{commodity}"
            # Convert datetime to ISO format for JSON serialization
            snapshot_dict = asdict(snapshot)
            snapshot_dict["last_transaction"] = snapshot.last_transaction.isoformat()
            
            await self.redis.setex(
                key,
                self.cache_ttl,
                json.dumps(snapshot_dict)
            )
            
        except Exception as e:
            logger.error(f"Cache storage error: {e}")
    
    def _create_default_snapshot(self, commodity: str) -> MarketSnapshot:
        """Create default snapshot when no data available (prices from RESOURCE_TYPES.md)"""
        base_prices = {
            # Core Commodities (7) - midpoint of price ranges
            "ORE": 30.0,                    # 15-45 credits
            "BASIC_FOOD": 16.5,             # 8-25 credits
            "GOURMET_FOOD": 50.0,           # 30-70 credits
            "FUEL": 40.0,                   # 20-60 credits
            "TECHNOLOGY": 85.0,             # 50-120 credits
            "EXOTIC_TECHNOLOGY": 225.0,     # 150-300 credits
            "LUXURY_GOODS": 137.5,          # 75-200 credits
            # Strategic Resources (4)
            "POPULATION": 50.0,             # 50 credits fixed
            "QUANTUM_SHARDS": 500.0,        # Very rare
            "QUANTUM_CRYSTALS": 5000.0,     # Extremely rare
            "COMBAT_DRONES": 1000.0,        # 1000 credits fixed
            # Rare Materials (2)
            "PRISMATIC_ORE": 2000.0,        # Extremely rare
            "PHOTONIC_CRYSTALS": 1500.0     # Very rare
        }
        
        base_price = base_prices.get(commodity, 100.0)
        
        return MarketSnapshot(
            commodity=commodity,
            current_price=base_price,
            volume_24h=0,
            high_24h=base_price,
            low_24h=base_price,
            price_change_24h=0.0,
            price_change_percent=0.0,
            last_transaction=datetime.now(UTC),
            bid_ask_spread=0.0,
            market_depth={"bids": [], "asks": []},
            sector_prices={},
            ai_prediction=None
        )
    
    # =============================================================================
    # PERFORMANCE MONITORING
    # =============================================================================
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get service performance metrics"""
        avg_query_time = sum(self.query_times) / len(self.query_times) if self.query_times else 0
        cache_hit_rate = self.cache_hits / (self.cache_hits + self.cache_misses) if (self.cache_hits + self.cache_misses) > 0 else 0
        
        return {
            "avg_query_time_ms": round(avg_query_time * 1000, 2),
            "cache_hit_rate": round(cache_hit_rate * 100, 2),
            "total_queries": self.cache_hits + self.cache_misses,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses
        }
    
    # =============================================================================
    # REDIS PUB/SUB
    # =============================================================================
    
    async def publish_market_update(self, commodity: str, snapshot: MarketSnapshot):
        """Publish market update to Redis pub/sub for broadcasting"""
        if not self.redis:
            return

        try:
            # Use pub/sub service for better management
            from src.services.redis_pubsub_service import get_pubsub_service
            pubsub_service = await get_pubsub_service()

            # Publish through the service
            subscribers = await pubsub_service.publish_market_update(
                commodity=commodity,
                market_data=snapshot.to_dict()
            )

            logger.debug(f"Published {commodity} update to {subscribers} subscribers")

        except Exception as e:
            logger.error(f"Error publishing market update: {e}")

    # ~1s batching window per (station, commodity), keyed in-process so the
    # synchronous trade path can fire updates without flooding pub/sub when a
    # commodity is hammered. Mirrors the cache_ttl real-time cadence.
    _last_trade_publish: Dict[str, float] = {}

    async def publish_trade_tick(
        self,
        station_id: str,
        commodity: str,
        buy_price: int,
        sell_price: int,
        quantity: int,
    ) -> None:
        """Publish a post-trade market tick for a single station/commodity.

        Built for the SYNCHRONOUS buy/sell route (no AsyncSession needed): the
        caller already holds fresh prices after the trade, so we publish them
        directly instead of re-querying. Respects a ~1s batching window per
        (station, commodity) so a hot commodity does not flood subscribers.

        Fully defensive — a publish hiccup must never affect the trade. The
        route awaits this AFTER its own commit, so failure here is cosmetic."""
        if not self.redis:
            return
        try:
            key = f"{station_id}:{commodity}"
            now = asyncio.get_event_loop().time()
            last = self._last_trade_publish.get(key, 0.0)
            if now - last < self.market_update_interval:
                return  # within the batching window — skip this tick
            self._last_trade_publish[key] = now

            from src.services.redis_pubsub_service import get_pubsub_service
            pubsub_service = await get_pubsub_service()
            await pubsub_service.publish_market_update(
                commodity=commodity,
                market_data={
                    "commodity": commodity,
                    "station_id": str(station_id),
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "quantity": quantity,
                    "current_price": (buy_price + sell_price) // 2,
                    "last_transaction": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as e:
            logger.error(f"Error publishing trade tick for {commodity}: {e}")


# Singleton instance
_market_service = None


def get_market_service(redis_client: redis.Redis = None) -> RealTimeMarketService:
    """Get or create market service instance"""
    global _market_service
    if _market_service is None:
        _market_service = RealTimeMarketService(redis_client)
    return _market_service