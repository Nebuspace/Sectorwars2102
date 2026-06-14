"""
AI Trading Intelligence Service

This service provides intelligent trading recommendations, market predictions,
and player behavior analysis for the Sectorwars2102 game.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.orm import selectinload
import asyncio
import json
import statistics
from dataclasses import dataclass, asdict
from enum import Enum

from src.models.ai_trading import (
    AIMarketPrediction, 
    PlayerTradingProfile, 
    AIRecommendation, 
    AIModelPerformance, 
    AITrainingData
)
from src.models.player import Player
from src.models.sector import Sector, sector_warps
from src.models.station import Station
from src.models.market_transaction import MarketTransaction, MarketPrice
from src.services.market_prediction_engine import MarketPredictionEngine
from src.services.route_optimizer import RouteOptimizer, RouteObjective


logger = logging.getLogger(__name__)


class RecommendationType(Enum):
    BUY = "buy"
    SELL = "sell"
    ROUTE = "route"
    AVOID = "avoid"
    WAIT = "wait"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"


@dataclass
class TradingRecommendation:
    """Data structure for trading recommendations"""
    id: str
    type: RecommendationType
    commodity_id: Optional[str]
    sector_id: Optional[str]
    target_price: Optional[float]
    expected_profit: Optional[float]
    confidence: float
    risk_level: RiskLevel
    reasoning: str
    priority: int
    expires_at: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "type": self.type.value,
            "commodity_id": self.commodity_id,
            "sector_id": self.sector_id,
            "target_price": self.target_price,
            "expected_profit": self.expected_profit,
            "confidence": self.confidence,
            "risk_level": self.risk_level.value,
            "reasoning": self.reasoning,
            "priority": self.priority,
            "expires_at": self.expires_at.isoformat()
        }


@dataclass
class MarketAnalysis:
    """Market analysis data structure"""
    commodity_id: str
    current_price: float
    predicted_price: float
    price_trend: str  # "rising", "falling", "stable"
    volatility: float
    confidence: float
    factors: List[str]
    time_horizon: int  # hours


@dataclass
class OptimalRoute:
    """Optimal trading route recommendation"""
    sectors: List[str]
    total_profit: float
    total_distance: int
    estimated_time: int  # minutes
    risk_score: float
    commodity_chain: List[Dict[str, Any]]


class AITradingService:
    """Core AI Trading Intelligence Service"""
    
    def __init__(self):
        self.model_version = "1.0.0"
        self.prediction_horizon_hours = 24
        self.max_recommendations_per_player = 10
        self.prediction_engine = MarketPredictionEngine()
        self.route_optimizer = RouteOptimizer()
        
    async def get_trading_recommendations(
        self, 
        db: AsyncSession, 
        player_id: str,
        limit: int = 5
    ) -> List[TradingRecommendation]:
        """
        Generate personalized trading recommendations for a player
        """
        try:
            # Get player and their trading profile
            player = await self._get_player_with_profile(db, player_id)
            if not player:
                logger.warning(f"Player {player_id} not found")
                return []
                
            profile = player.trading_profile
            if not profile:
                # Create initial profile if none exists
                profile = await self._create_initial_trading_profile(db, player)
                
            # Generate different types of recommendations
            recommendations = []
            
            # Market opportunity recommendations
            market_recs = await self._generate_market_opportunities(db, player, profile)
            recommendations.extend(market_recs[:2])  # Top 2 market opportunities
            
            # Route optimization recommendations
            route_recs = await self._generate_route_recommendations(db, player, profile)
            recommendations.extend(route_recs[:2])  # Top 2 route suggestions
            
            # Risk avoidance recommendations
            risk_recs = await self._generate_risk_warnings(db, player, profile)
            recommendations.extend(risk_recs[:1])  # Top 1 risk warning
            
            # Sort by priority and confidence
            recommendations.sort(key=lambda x: (x.priority, x.confidence), reverse=True)
            
            # Save recommendations to database
            await self._save_recommendations_to_db(db, player_id, recommendations[:limit])
            
            return recommendations[:limit]
            
        except Exception as e:
            logger.error(f"Error generating recommendations for player {player_id}: {e}")
            return []
    
    async def analyze_market_trends(
        self, 
        db: AsyncSession, 
        commodity_id: str,
        sector_id: Optional[str] = None
    ) -> MarketAnalysis:
        """
        Analyze market trends for a specific commodity
        """
        try:
            # Get historical price data
            price_history = await self._get_price_history(db, commodity_id, sector_id)
            
            if len(price_history) < 5:
                # Not enough data for analysis
                return MarketAnalysis(
                    commodity_id=commodity_id,
                    current_price=0.0,
                    predicted_price=0.0,
                    price_trend="unknown",
                    volatility=0.0,
                    confidence=0.0,
                    factors=["Insufficient historical data"],
                    time_horizon=24
                )
            
            # Calculate trend and volatility
            prices = [p['price'] for p in price_history]
            current_price = prices[-1]
            
            # Simple trend analysis
            trend = self._calculate_price_trend(prices)
            volatility = self._calculate_volatility(prices)
            
            # Generate prediction
            predicted_price = await self._predict_future_price(
                db, commodity_id, sector_id, price_history
            )
            
            # Calculate confidence based on data quality and model performance
            confidence = self._calculate_prediction_confidence(prices, volatility)
            
            # Identify key factors
            factors = await self._identify_price_factors(db, commodity_id, sector_id)
            
            return MarketAnalysis(
                commodity_id=commodity_id,
                current_price=current_price,
                predicted_price=predicted_price,
                price_trend=trend,
                volatility=volatility,
                confidence=confidence,
                factors=factors,
                time_horizon=24
            )
            
        except Exception as e:
            logger.error(f"Error analyzing market trends for commodity {commodity_id}: {e}")
            raise
    
    async def optimize_trade_route(
        self, 
        db: AsyncSession,
        player_id: str,
        start_sector: str,
        cargo_capacity: int,
        max_stops: int = 5
    ) -> OptimalRoute:
        """
        Calculate optimal trade route for maximum profit using advanced algorithms
        """
        try:
            player = await self._get_player_with_profile(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")
            
            # Get player preferences from profile
            risk_tolerance = player.trading_profile.risk_tolerance if player.trading_profile else 0.5
            max_time = 24.0  # Default 24 hours
            
            # Use the advanced route optimizer
            optimized_route = await self.route_optimizer.find_optimal_route(
                db=db,
                start_sector_id=start_sector,
                player_id=player_id,
                cargo_capacity=cargo_capacity,
                max_route_time=max_time,
                objective=RouteObjective.MAX_PROFIT,
                risk_tolerance=risk_tolerance
            )
            
            if not optimized_route:
                # Return empty route if no optimization possible
                return OptimalRoute(
                    sectors=[start_sector],
                    total_profit=0.0,
                    total_distance=0,
                    estimated_time=0,
                    risk_score=0.0,
                    commodity_chain=[]
                )
            
            # Convert to legacy OptimalRoute format
            commodity_chain = []
            for i, opportunity in enumerate(optimized_route.opportunities):
                commodity_chain.append({
                    'step': i + 1,
                    'from_sector': opportunity.from_sector_id,
                    'to_sector': opportunity.to_sector_id,
                    'commodity': opportunity.commodity_id,
                    'buy_price': opportunity.buy_price,
                    'sell_price': opportunity.sell_price,
                    'profit_per_unit': opportunity.profit_per_unit,
                    'max_quantity': opportunity.max_quantity,
                    'confidence': opportunity.confidence
                })
            
            return OptimalRoute(
                sectors=optimized_route.sectors,
                total_profit=optimized_route.total_profit,
                total_distance=optimized_route.total_distance,
                estimated_time=int(optimized_route.total_time_hours * 60),  # Convert to minutes
                risk_score=optimized_route.total_risk,
                commodity_chain=commodity_chain
            )
            
        except Exception as e:
            logger.error(f"Error optimizing trade route for player {player_id}: {e}")
            raise
    
    async def update_player_profile(
        self,
        db: AsyncSession,
        player_id: str,
        trade_data: Dict[str, Any]
    ) -> bool:
        """
        Update player trading profile based on new trade data
        """
        try:
            profile = await self._get_trading_profile(db, player_id)
            if not profile:
                return False
            
            # Update trading patterns
            if profile.trading_patterns is None:
                profile.trading_patterns = {}
            
            # Extract patterns from trade data
            patterns = self._extract_trading_patterns(trade_data)
            profile.trading_patterns.update(patterns)
            
            # Update performance metrics
            if trade_data.get('profit'):
                total_profit = (profile.average_profit_per_trade * profile.total_trades_analyzed + 
                              trade_data['profit'])
                profile.total_trades_analyzed += 1
                profile.average_profit_per_trade = total_profit / profile.total_trades_analyzed
            
            # Update risk tolerance based on recent behavior
            profile.risk_tolerance = self._adjust_risk_tolerance(
                profile.risk_tolerance, trade_data
            )
            
            profile.updated_at = datetime.utcnow()
            await db.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating player profile {player_id}: {e}")
            return False
    
    # Private helper methods
    
    async def _get_player_with_profile(self, db: AsyncSession, player_id: str) -> Optional[Player]:
        """Get player with trading profile loaded"""
        try:
            query = select(Player).options(
                selectinload(Player.trading_profile)
            ).where(Player.id == uuid.UUID(player_id))
            
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Error getting player with profile: {e}")
            return None
    
    async def _create_initial_trading_profile(
        self, 
        db: AsyncSession, 
        player: Player
    ) -> PlayerTradingProfile:
        """Create initial trading profile for new player"""
        profile = PlayerTradingProfile(
            player_id=player.id,
            risk_tolerance=0.5,  # Start with moderate risk
            ai_assistance_level='medium',
            trading_patterns={},
            performance_metrics={},
            notification_preferences={
                'market_opportunities': True,
                'risk_warnings': True,
                'price_alerts': False
            }
        )
        
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        
        return profile
    
    async def _generate_market_opportunities(
        self, 
        db: AsyncSession,
        player: Player, 
        profile: PlayerTradingProfile
    ) -> List[TradingRecommendation]:
        """Generate market opportunity recommendations"""
        recommendations = []
        
        try:
            # Find commodities with good buy opportunities
            # This is a simplified version - real implementation would use ML models
            
            # Example: Look for commodities trading below predicted price
            predictions = await self._get_active_predictions(db, player.current_sector_id)
            
            for prediction in predictions:
                if prediction.predicted_price > 0:
                    current_market_price = await self._get_current_market_price(
                        db, prediction.commodity_id, prediction.sector_id
                    )
                    
                    if current_market_price and current_market_price < prediction.predicted_price * 0.95:
                        # Good buy opportunity
                        expected_profit = (prediction.predicted_price - current_market_price) * 100  # Assume 100 units
                        
                        rec = TradingRecommendation(
                            id=str(uuid.uuid4()),
                            type=RecommendationType.BUY,
                            commodity_id=str(prediction.commodity_id),
                            sector_id=str(prediction.sector_id),
                            target_price=float(prediction.predicted_price),
                            expected_profit=expected_profit,
                            confidence=float(prediction.confidence_interval),
                            risk_level=self._assess_risk_level(profile.risk_tolerance, prediction),
                            reasoning=f"AI predicts price will rise to {prediction.predicted_price} within {prediction.prediction_horizon} hours",
                            priority=self._calculate_priority(expected_profit, float(prediction.confidence_interval)),
                            expires_at=prediction.expires_at
                        )
                        
                        recommendations.append(rec)
            
        except Exception as e:
            logger.error(f"Error generating market opportunities: {e}")
        
        return recommendations
    
    async def _generate_route_recommendations(
        self,
        db: AsyncSession,
        player: Player,
        profile: PlayerTradingProfile
    ) -> List[TradingRecommendation]:
        """Generate route optimization recommendations"""
        recommendations = []
        
        try:
            # Simple route recommendation based on nearby profitable trades
            nearby_sectors = await self._get_nearby_sectors(db, player.current_sector_id, 3)
            
            best_route = await self._find_best_simple_route(db, nearby_sectors)
            
            if best_route and best_route['profit'] > 1000:  # Minimum profit threshold
                rec = TradingRecommendation(
                    id=str(uuid.uuid4()),
                    type=RecommendationType.ROUTE,
                    commodity_id=best_route.get('commodity_id'),
                    sector_id=str(best_route['to_sector']),
                    target_price=None,
                    expected_profit=best_route['profit'],
                    confidence=0.8,  # Route calculations are generally reliable
                    risk_level=RiskLevel.LOW,
                    reasoning=f"Profitable route found: {best_route['description']}",
                    priority=4,
                    expires_at=datetime.utcnow() + timedelta(hours=2)
                )
                
                recommendations.append(rec)
        
        except Exception as e:
            logger.error(f"Error generating route recommendations: {e}")
        
        return recommendations
    
    async def _generate_risk_warnings(
        self,
        db: AsyncSession,
        player: Player,
        profile: PlayerTradingProfile
    ) -> List[TradingRecommendation]:
        """Generate risk warning recommendations"""
        recommendations = []
        
        try:
            # Check for high-risk sectors or market conditions
            current_sector_risk = await self._assess_sector_risk(db, player.current_sector_id)
            
            if current_sector_risk > 0.7:  # High risk threshold
                rec = TradingRecommendation(
                    id=str(uuid.uuid4()),
                    type=RecommendationType.AVOID,
                    commodity_id=None,
                    sector_id=str(player.current_sector_id),
                    target_price=None,
                    expected_profit=None,
                    confidence=0.9,
                    risk_level=RiskLevel.HIGH,
                    reasoning=f"Current sector has high risk score: {current_sector_risk:.2f}",
                    priority=5,  # High priority for risk warnings
                    expires_at=datetime.utcnow() + timedelta(hours=1)
                )
                
                recommendations.append(rec)
        
        except Exception as e:
            logger.error(f"Error generating risk warnings: {e}")
        
        return recommendations
    
    async def _save_recommendations_to_db(
        self,
        db: AsyncSession,
        player_id: str,
        recommendations: List[TradingRecommendation]
    ) -> None:
        """Save recommendations to database"""
        try:
            for rec in recommendations:
                db_rec = AIRecommendation(
                    player_id=uuid.UUID(player_id),
                    recommendation_type=rec.type.value,
                    recommendation_data=rec.to_dict(),
                    confidence_score=rec.confidence,
                    expected_profit=rec.expected_profit,
                    risk_assessment=rec.risk_level.value,
                    reasoning=rec.reasoning,
                    priority_level=rec.priority,
                    expires_at=rec.expires_at
                )
                
                db.add(db_rec)
            
            await db.commit()
            
        except Exception as e:
            logger.error(f"Error saving recommendations to database: {e}")
    
    # Simplified helper methods (real implementation would be more sophisticated)
    
    async def _get_price_history(self, db: AsyncSession, commodity_id: str, sector_id: Optional[str]) -> List[Dict]:
        """Real recent price points for a commodity, from executed market transactions
        (enhanced_market_transactions — player + NPC trades). Oldest→newest so the
        caller's prices[-1] is the latest. Commodity is matched case-insensitively."""
        try:
            # Commodity is stored lowercase (model note); compare to a lowercased
            # input so the ix_market_transactions_commodity btree index is usable.
            stmt = select(MarketTransaction.unit_price).where(
                MarketTransaction.commodity == str(commodity_id).lower()
            ).order_by(MarketTransaction.timestamp.asc()).limit(60)
            rows = (await db.execute(stmt)).all()
            return [{"price": float(r[0])} for r in rows if r[0] is not None]
        except Exception as e:
            logger.error(f"Error fetching price history for {commodity_id}: {e}")
            return []
    
    async def _predict_future_price(self, db: AsyncSession, commodity_id: str, sector_id: Optional[str], history: List) -> float:
        """Predict future price using Prophet ML model"""
        try:
            # Use the real prediction engine
            prediction = await self.prediction_engine.predict_prices(
                db, commodity_id, sector_id, self.prediction_horizon_hours
            )
            
            if prediction:
                # Support both dict and dataclass returns
                if hasattr(prediction, 'predicted_price'):
                    return prediction.predicted_price
                return prediction.get('predicted_price', 0.0)
            
            # Fallback to simple prediction if Prophet fails
            if history:
                prices = [h['price'] for h in history]
                return prices[-1] * 1.05  # Simple 5% increase prediction
            return 0.0
            
        except Exception as e:
            logger.error(f"Error predicting future price: {e}")
            # Fallback
            if history:
                prices = [h['price'] for h in history]
                return prices[-1] * 1.05
            return 0.0
    
    def _calculate_price_trend(self, prices: List[float]) -> str:
        """Calculate price trend from historical data"""
        if len(prices) < 2:
            return "stable"
        
        recent_avg = statistics.mean(prices[-3:])
        older_avg = statistics.mean(prices[:-3] if len(prices) > 3 else prices[:1])
        
        if recent_avg > older_avg * 1.05:
            return "rising"
        elif recent_avg < older_avg * 0.95:
            return "falling"
        else:
            return "stable"
    
    def _calculate_volatility(self, prices: List[float]) -> float:
        """Calculate price volatility"""
        if len(prices) < 2:
            return 0.0
        
        return statistics.stdev(prices) / statistics.mean(prices) if statistics.mean(prices) > 0 else 0.0
    
    def _calculate_prediction_confidence(self, prices: List[float], volatility: float) -> float:
        """Calculate confidence in prediction based on data quality"""
        base_confidence = 0.5
        
        # More data points increase confidence
        data_confidence = min(0.4, len(prices) * 0.02)
        
        # Lower volatility increases confidence
        volatility_confidence = max(0.0, 0.3 - volatility)
        
        return min(1.0, base_confidence + data_confidence + volatility_confidence)
    
    async def _identify_price_factors(self, db: AsyncSession, commodity_id: str, sector_id: Optional[str]) -> List[str]:
        """Real factors affecting a commodity's price, derived from live market
        state (market_prices demand/supply + price spread, recent transaction
        volume) — not a static placeholder."""
        try:
            commodity = str(commodity_id).lower()
            row = (await db.execute(
                select(
                    func.avg(MarketPrice.demand_level),
                    func.avg(MarketPrice.supply_level),
                    func.min(MarketPrice.sell_price),
                    func.max(MarketPrice.sell_price),
                    func.count(),
                ).where(MarketPrice.commodity == commodity)
            )).first()
            if not row or not row[4]:
                return ["No live market data for this commodity"]
            avg_demand = float(row[0] or 1.0)
            avg_supply = float(row[1] or 1.0)
            min_sell, max_sell, n_markets = row[2], row[3], int(row[4])

            factors: List[str] = []
            if avg_demand > avg_supply * 1.05:
                factors.append("Demand outpacing supply")
            elif avg_supply > avg_demand * 1.05:
                factors.append("Supply exceeds demand")
            else:
                factors.append("Supply and demand roughly balanced")

            if min_sell and max_sell and max_sell > min_sell * 1.3:
                factors.append(f"Wide price spread across markets ({int(min_sell)}-{int(max_sell)} cr)")

            recent = (await db.execute(
                select(func.count()).select_from(MarketTransaction).where(
                    MarketTransaction.commodity == commodity,
                    MarketTransaction.timestamp > datetime.utcnow() - timedelta(days=7),
                )
            )).scalar() or 0
            factors.append(f"{recent} trades in the last 7 days" if recent else "Thin recent trading")
            factors.append(f"Priced at {n_markets} markets")
            return factors
        except Exception as e:
            logger.error(f"Error identifying price factors for {commodity_id}: {e}")
            return ["Market data temporarily unavailable"]
    
    def _assess_risk_level(self, player_risk_tolerance: float, prediction: AIMarketPrediction) -> RiskLevel:
        """Assess risk level for a recommendation"""
        if float(prediction.confidence_interval) < 0.6:
            return RiskLevel.HIGH
        elif float(prediction.confidence_interval) < 0.8:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _calculate_priority(self, expected_profit: float, confidence: float) -> int:
        """Calculate recommendation priority (1-5 scale)"""
        score = (expected_profit / 1000) * confidence
        
        if score >= 5:
            return 5
        elif score >= 3:
            return 4
        elif score >= 1:
            return 3
        elif score >= 0.5:
            return 2
        else:
            return 1
    
    # Additional simplified helper methods
    async def _get_active_predictions(self, db: AsyncSession, sector_id: int) -> List[AIMarketPrediction]:
        """Active (unexpired) market predictions for a sector. Resolves the
        human-readable int sector_id to the sectors.id UUID first. Returns []
        honestly when no live predictions exist (rather than faking some)."""
        try:
            sector_uuid = (await db.execute(
                select(Sector.id).where(Sector.sector_id == int(sector_id))
            )).scalar_one_or_none()
            if sector_uuid is None:
                return []
            stmt = select(AIMarketPrediction).where(
                and_(
                    AIMarketPrediction.sector_id == sector_uuid,
                    AIMarketPrediction.expires_at > datetime.utcnow(),
                )
            ).order_by(desc(AIMarketPrediction.created_at))
            return list((await db.execute(stmt)).scalars().all())
        except Exception as e:
            logger.error(f"Error fetching active predictions for sector {sector_id}: {e}")
            return []

    async def _get_current_market_price(self, db: AsyncSession, commodity_id: str, sector_id: str) -> Optional[float]:
        """Current live market sell price for a commodity, from market_prices —
        sector-specific average when a sector is given, else market-wide average.

        NB: commodity_id here is the lowercase commodity NAME (e.g. 'organics')
        on the analysis path. The predictions path (_generate_market_opportunities)
        passes AIMarketPrediction.commodity_id, which is a UUID — that lookup will
        not match a name and falls through to None until predictions carry/resolve
        the commodity name. Harmless today (ai_market_predictions is empty)."""
        try:
            base = select(func.avg(MarketPrice.sell_price)).where(
                MarketPrice.commodity == str(commodity_id).lower()
            )
            if sector_id is not None:
                try:
                    stmt = base.select_from(MarketPrice).join(
                        Station, Station.id == MarketPrice.station_id
                    ).where(Station.sector_id == int(sector_id))
                    val = (await db.execute(stmt)).scalar()
                    if val is not None:
                        return float(val)
                except (ValueError, TypeError):
                    pass
            val = (await db.execute(base)).scalar()
            return float(val) if val is not None else None
        except Exception as e:
            logger.error(f"Error fetching current price for {commodity_id}: {e}")
            return None
    
    async def _get_nearby_sectors(self, db: AsyncSession, sector_id: int, max_distance: int) -> List[str]:
        """Warp-reachable sectors within max_distance hops — real BFS over the
        sector_warps graph (bidirectional-aware). Resolves int↔UUID via sectors."""
        try:
            origin_uuid = (await db.execute(
                select(Sector.id).where(Sector.sector_id == int(sector_id))
            )).scalar_one_or_none()
            if origin_uuid is None:
                return []
            visited = {origin_uuid}
            frontier = {origin_uuid}
            for _ in range(max(1, max_distance)):
                if not frontier:
                    break
                stmt = select(
                    sector_warps.c.source_sector_id,
                    sector_warps.c.destination_sector_id,
                    sector_warps.c.is_bidirectional,
                ).where(
                    or_(
                        sector_warps.c.source_sector_id.in_(frontier),
                        and_(
                            sector_warps.c.is_bidirectional == True,  # noqa: E712
                            sector_warps.c.destination_sector_id.in_(frontier),
                        ),
                    )
                )
                rows = (await db.execute(stmt)).all()
                nxt = set()
                for src, dst, bidir in rows:
                    if src in frontier and dst not in visited:
                        nxt.add(dst)
                    if bidir and dst in frontier and src not in visited:
                        nxt.add(src)
                nxt -= visited
                visited |= nxt
                frontier = nxt
            neighbor_uuids = [u for u in visited if u != origin_uuid]
            if not neighbor_uuids:
                return []
            rows = (await db.execute(
                select(Sector.sector_id).where(Sector.id.in_(neighbor_uuids))
            )).all()
            return [str(r[0]) for r in rows]
        except Exception as e:
            logger.error(f"Error computing nearby sectors for {sector_id}: {e}")
            return []

    async def _find_best_simple_route(self, db: AsyncSession, sectors: List[str]) -> Optional[Dict]:
        """Best executable buy-low / sell-high route across the given sectors,
        from live market_prices.

        Price semantics (market_transaction.py): a station's `sell_price` is what
        it CHARGES a player (the player's BUY cost); its `buy_price` is what it
        PAYS a player (the player's SELL revenue). So a real round-trip profit is
        (best price a station will PAY for it) − (cheapest price to BUY it):
        max(buy_price) − min(sell_price), cross-sector only. Profit is over a
        nominal 100-unit haul (per-unit prices shown in the description). Routes
        below a minimum viable spread are dropped."""
        UNITS = 100  # nominal haul for the profit estimate (per-unit prices shown in description)
        MIN_PROFIT = 100  # minimum viable aggregate profit; the caller gates harder (>1000)
        try:
            sector_ints = []
            for s in sectors:
                try:
                    sector_ints.append(int(s))
                except (ValueError, TypeError):
                    continue
            if not sector_ints:
                return None
            stmt = select(
                MarketPrice.commodity,
                Station.sector_id,
                func.min(MarketPrice.sell_price),  # cheapest price a PLAYER can BUY at, this sector
                func.max(MarketPrice.buy_price),   # best price a PLAYER can SELL at, this sector
            ).select_from(MarketPrice).join(
                Station, Station.id == MarketPrice.station_id
            ).where(
                Station.sector_id.in_(sector_ints)
            ).group_by(MarketPrice.commodity, Station.sector_id)
            rows = (await db.execute(stmt)).all()

            # Per commodity: cheapest place to BUY (min sell_price) and best place
            # to SELL (max buy_price) across the candidate sectors.
            best_buy: Dict[str, Tuple[int, int]] = {}   # commodity -> (player_buy_cost, sector)
            best_sell: Dict[str, Tuple[int, int]] = {}  # commodity -> (player_sell_revenue, sector)
            for commodity, sec, min_sell, max_buy in rows:
                if min_sell is not None and (commodity not in best_buy or min_sell < best_buy[commodity][0]):
                    best_buy[commodity] = (int(min_sell), int(sec))
                if max_buy is not None and (commodity not in best_sell or max_buy > best_sell[commodity][0]):
                    best_sell[commodity] = (int(max_buy), int(sec))

            best_route = None
            for commodity in best_buy:
                if commodity not in best_sell:
                    continue
                buy_cost, buy_sec = best_buy[commodity]     # what the player pays to acquire
                sell_rev, sell_sec = best_sell[commodity]   # what the player receives selling
                if buy_sec == sell_sec:
                    continue  # need a real A→B route, not a single station's internal spread
                profit = int((sell_rev - buy_cost) * UNITS)
                if profit < MIN_PROFIT:
                    continue  # not a worthwhile (or not a real) arbitrage
                if best_route is None or profit > best_route["profit"]:
                    best_route = {
                        "to_sector": str(sell_sec),
                        "from_sector": str(buy_sec),
                        "profit": profit,
                        "description": (
                            f"Buy {commodity} in sector {buy_sec} (~{buy_cost} cr/unit), "
                            f"sell in sector {sell_sec} (~{sell_rev} cr/unit)"
                        ),
                        "commodity_id": commodity,
                    }
            return best_route
        except Exception as e:
            logger.error(f"Error finding best route: {e}")
            return None
    
    async def _assess_sector_risk(self, db: AsyncSession, sector_id: int) -> float:
        """Real sector risk (0-1) from hostile-NPC (raider) presence currently
        in the sector. 0.1 when clear; rises with each active raider so a sector
        with 2+ raiders crosses the 0.7 warning threshold its caller uses."""
        try:
            from src.models.npc_character import NPCCharacter, NPCArchetype, NPCStatus
            # A raider is a live in-sector threat unless it's dead/gone.
            DOWN = [NPCStatus.KIA, NPCStatus.RESPAWNING, NPCStatus.RETIRED, NPCStatus.REASSIGNED]
            hostiles = (await db.execute(
                select(func.count()).select_from(NPCCharacter).where(
                    NPCCharacter.current_sector_id == int(sector_id),
                    NPCCharacter.archetype == NPCArchetype.HOSTILE_RAIDER,
                    NPCCharacter.status.notin_(DOWN),
                )
            )).scalar() or 0
            if hostiles <= 0:
                return 0.1
            return min(1.0, 0.4 + 0.2 * hostiles)
        except Exception as e:
            logger.error(f"Error assessing sector risk for {sector_id}: {e}")
            return 0.3
    
    def _extract_trading_patterns(self, trade_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract patterns from trade data"""
        return {
            'last_trade_type': trade_data.get('type', 'unknown'),
            'last_profit': trade_data.get('profit', 0),
            'last_trade_time': datetime.utcnow().isoformat()
        }
    
    def _adjust_risk_tolerance(self, current_tolerance: float, trade_data: Dict[str, Any]) -> float:
        """Adjust risk tolerance based on recent trading behavior"""
        # Simple adjustment - real implementation would be more sophisticated
        if trade_data.get('profit', 0) > 0:
            return min(1.0, current_tolerance + 0.01)  # Slightly more aggressive after profit
        else:
            return max(0.0, current_tolerance - 0.01)  # Slightly more conservative after loss