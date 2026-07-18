"""
AI Trading Intelligence API Routes

Provides endpoints for AI-powered trading recommendations, market analysis,
and player behavior profiling.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from pydantic import BaseModel, UUID4, Field
import uuid

from src.core.database import get_async_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ai_trading import (
    AIMarketPrediction,
    PlayerTradingProfile,
    AIRecommendation,
    AIModelPerformance
)
from src.models.route_optimization_run import RouteOptimizationRun
from src.services.ai_trading_service import AITradingService, TradingRecommendation, MarketAnalysis, OptimalRoute

router = APIRouter(prefix="/ai", tags=["AI Trading Intelligence"])
logger = logging.getLogger(__name__)

# Initialize AI service lazily to prevent import-time issues during testing
ai_service = None

def get_ai_service():
    """Get AI service instance (lazy initialization)"""
    global ai_service
    if ai_service is None:
        ai_service = AITradingService()
    return ai_service


# Pydantic models for request/response
class TradingRecommendationResponse(BaseModel):
    id: str
    type: str
    commodity_id: Optional[str] = None
    sector_id: Optional[str] = None
    target_price: Optional[float] = None
    expected_profit: Optional[float] = None
    confidence: float
    risk_level: str
    reasoning: str
    priority: int
    expires_at: datetime
    
    class Config:
        from_attributes = True


class MarketAnalysisResponse(BaseModel):
    commodity_id: str
    current_price: float
    predicted_price: float
    price_trend: str
    volatility: float
    confidence: float
    factors: List[str]
    time_horizon: int
    
    class Config:
        from_attributes = True


class PlayerTradingProfileResponse(BaseModel):
    player_id: UUID4
    risk_tolerance: float
    ai_assistance_level: str
    average_profit_per_trade: float
    total_trades_analyzed: int
    preferred_commodities: Optional[Dict[str, Any]] = None
    trading_patterns: Optional[Dict[str, Any]] = None
    performance_metrics: Optional[Dict[str, Any]] = None
    last_active_sector: Optional[UUID4] = None
    
    class Config:
        from_attributes = True


class AIPreferences(BaseModel):
    ai_assistance_level: str = Field(..., pattern="^(minimal|medium|full)$")
    risk_tolerance: float = Field(..., ge=0.0, le=1.0)
    notification_preferences: Optional[Dict[str, bool]] = None


class RecommendationFeedback(BaseModel):
    accepted: bool
    feedback_score: Optional[int] = Field(None, ge=1, le=5)
    feedback_text: Optional[str] = None


class RouteOptimizationRequest(BaseModel):
    start_sector: str
    cargo_capacity: int = Field(..., gt=0)
    max_stops: int = Field(5, ge=1, le=10)


class TradeDataUpdate(BaseModel):
    trade_type: str
    commodity_id: Optional[str] = None
    sector_id: Optional[str] = None
    profit: Optional[float] = None
    risk_taken: Optional[float] = None
    additional_data: Optional[Dict[str, Any]] = None


# AI Trading Recommendations
@router.get("/recommendations", response_model=List[TradingRecommendationResponse])
async def get_trading_recommendations(
    limit: int = Query(5, ge=1, le=20),
    include_expired: bool = Query(False),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get personalized AI trading recommendations for the current player
    """
    try:
        # Get fresh recommendations from AI service
        recommendations = await get_ai_service().get_trading_recommendations(
            db, str(current_player.id), limit
        )
        
        # Convert to response format
        response_data = []
        for rec in recommendations:
            if not include_expired and rec.expires_at < datetime.utcnow():
                continue
                
            response_data.append(TradingRecommendationResponse(
                id=rec.id,
                type=rec.type.value,
                commodity_id=rec.commodity_id,
                sector_id=rec.sector_id,
                target_price=rec.target_price,
                expected_profit=rec.expected_profit,
                confidence=rec.confidence,
                risk_level=rec.risk_level.value,
                reasoning=rec.reasoning,
                priority=rec.priority,
                expires_at=rec.expires_at
            ))
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error getting recommendations for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate trading recommendations"
        )


@router.get("/recommendations/history", response_model=List[TradingRecommendationResponse])
async def get_recommendation_history(
    days_back: int = Query(7, ge=1, le=30),
    recommendation_type: Optional[str] = Query(None),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get historical AI recommendations for the current player
    """
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        query = select(AIRecommendation).where(
            and_(
                AIRecommendation.player_id == current_player.id,
                AIRecommendation.created_at >= cutoff_date
            )
        ).order_by(desc(AIRecommendation.created_at))
        
        if recommendation_type:
            query = query.where(AIRecommendation.recommendation_type == recommendation_type)
        
        result = await db.execute(query)
        recommendations = result.scalars().all()
        
        response_data = []
        for rec in recommendations:
            rec_data = rec.recommendation_data
            response_data.append(TradingRecommendationResponse(
                id=str(rec.id),
                type=rec.recommendation_type,
                commodity_id=rec_data.get('commodity_id'),
                sector_id=rec_data.get('sector_id'),
                target_price=rec_data.get('target_price'),
                expected_profit=rec.expected_profit,
                confidence=float(rec.confidence_score),
                risk_level=rec.risk_assessment,
                reasoning=rec.reasoning or "No reasoning provided",
                priority=rec.priority_level,
                expires_at=rec.expires_at
            ))
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error getting recommendation history for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve recommendation history"
        )


@router.post("/recommendations/{recommendation_id}/feedback")
async def submit_recommendation_feedback(
    recommendation_id: UUID4,
    feedback: RecommendationFeedback,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Submit feedback on an AI recommendation
    """
    try:
        # Find the recommendation
        query = select(AIRecommendation).where(
            and_(
                AIRecommendation.id == recommendation_id,
                AIRecommendation.player_id == current_player.id
            )
        )
        
        result = await db.execute(query)
        recommendation = result.scalar_one_or_none()
        
        if not recommendation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recommendation not found"
            )
        
        # Update recommendation with feedback
        recommendation.accepted = feedback.accepted
        recommendation.acceptance_timestamp = datetime.utcnow()
        recommendation.feedback_score = feedback.feedback_score
        recommendation.feedback_text = feedback.feedback_text
        
        await db.commit()
        
        # Update player profile based on feedback
        await get_ai_service().update_player_profile(
            db, str(current_player.id), {
                'recommendation_feedback': {
                    'accepted': feedback.accepted,
                    'type': recommendation.recommendation_type,
                    'score': feedback.feedback_score
                }
            }
        )
        
        return {"message": "Feedback submitted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting feedback for recommendation {recommendation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit feedback"
        )


# Market Analysis
@router.get("/market-analysis/{commodity_id}", response_model=MarketAnalysisResponse)
async def get_market_analysis(
    commodity_id: str,
    sector_id: Optional[str] = Query(None),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get AI-powered market analysis for a specific commodity
    """
    try:
        analysis = await get_ai_service().analyze_market_trends(db, commodity_id, sector_id)
        
        return MarketAnalysisResponse(
            commodity_id=analysis.commodity_id,
            current_price=analysis.current_price,
            predicted_price=analysis.predicted_price,
            price_trend=analysis.price_trend,
            volatility=analysis.volatility,
            confidence=analysis.confidence,
            factors=analysis.factors,
            time_horizon=analysis.time_horizon
        )
        
    except Exception as e:
        logger.error(f"Error getting market analysis for commodity {commodity_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate market analysis"
        )


# Route Optimization
@router.post("/optimize-route")
async def optimize_trading_route(
    request: RouteOptimizationRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get AI-optimized trading route recommendations
    """
    try:
        optimal_route = await get_ai_service().optimize_trade_route(
            db,
            str(current_player.id),
            request.start_sector,
            request.cargo_capacity,
            request.max_stops
        )

        # Best-effort telemetry write for the NH18 admin feed (WO-SB-RO2).
        # Never allowed to fail the player's request.
        try:
            db.add(
                RouteOptimizationRun(
                    player_id=current_player.id,
                    objective="ai_trading",
                    start_sector=request.start_sector,
                    end_sector=None,
                    sectors=optimal_route.sectors,
                    total_profit=optimal_route.total_profit,
                    total_distance=optimal_route.total_distance,
                    # OptimalRoute.estimated_time is MINUTES (ai_trading_service.py:104,
                    # :288 `int(total_time_hours * 60)`) — convert to hours for this
                    # column, which every other objective already records in hours.
                    total_time_hours=optimal_route.estimated_time / 60.0,
                    cargo_efficiency=0.0,
                    route_confidence=0.0,
                )
            )
            await db.commit()
        except Exception as record_exc:
            logger.error(
                f"Failed to record route optimization run for player {current_player.id}: {record_exc}"
            )
            await db.rollback()

        return {
            "sectors": optimal_route.sectors,
            "total_profit": optimal_route.total_profit,
            "total_distance": optimal_route.total_distance,
            "estimated_time": optimal_route.estimated_time,
            "risk_score": optimal_route.risk_score,
            "commodity_chain": optimal_route.commodity_chain
        }
        
    except Exception as e:
        logger.error(f"Error optimizing route for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to optimize trading route"
        )


# Player Profile Management
@router.get("/profile", response_model=PlayerTradingProfileResponse)
async def get_player_trading_profile(
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get the current player's AI trading profile
    """
    try:
        query = select(PlayerTradingProfile).where(
            PlayerTradingProfile.player_id == current_player.id
        )
        
        result = await db.execute(query)
        profile = result.scalar_one_or_none()
        
        if not profile:
            # Create initial profile if none exists
            profile = PlayerTradingProfile(
                player_id=current_player.id,
                risk_tolerance=0.5,
                ai_assistance_level='medium'
            )
            db.add(profile)
            await db.commit()
            await db.refresh(profile)
        
        return PlayerTradingProfileResponse(
            player_id=profile.player_id,
            risk_tolerance=float(profile.risk_tolerance),
            ai_assistance_level=profile.ai_assistance_level,
            average_profit_per_trade=float(profile.average_profit_per_trade),
            total_trades_analyzed=profile.total_trades_analyzed,
            preferred_commodities=profile.preferred_commodities,
            trading_patterns=profile.trading_patterns,
            performance_metrics=profile.performance_metrics,
            last_active_sector=profile.last_active_sector
        )
        
    except Exception as e:
        logger.error(f"Error getting trading profile for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve trading profile"
        )


@router.put("/profile")
async def update_ai_preferences(
    preferences: AIPreferences,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Update AI preferences for the current player
    """
    try:
        query = select(PlayerTradingProfile).where(
            PlayerTradingProfile.player_id == current_player.id
        )
        
        result = await db.execute(query)
        profile = result.scalar_one_or_none()
        
        if not profile:
            profile = PlayerTradingProfile(player_id=current_player.id)
            db.add(profile)
        
        # Update preferences
        profile.ai_assistance_level = preferences.ai_assistance_level
        profile.risk_tolerance = preferences.risk_tolerance
        
        if preferences.notification_preferences:
            profile.notification_preferences = preferences.notification_preferences
        
        profile.updated_at = datetime.utcnow()
        
        await db.commit()
        
        return {"message": "AI preferences updated successfully"}
        
    except Exception as e:
        logger.error(f"Error updating AI preferences for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update AI preferences"
        )


@router.post("/profile/trade-update")
async def update_trading_data(
    trade_data: TradeDataUpdate,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Update player profile with new trade data for AI learning
    """
    try:
        success = await get_ai_service().update_player_profile(
            db, str(current_player.id), trade_data.dict()
        )
        
        if success:
            return {"message": "Trading data updated successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to update trading data"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating trading data for player {current_player.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update trading data"
        )


# AI Performance and Statistics
@router.get("/performance")
async def get_ai_performance_stats(
    days_back: int = Query(7, ge=1, le=30),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get AI performance statistics
    """
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        # Get recent model performance data
        query = select(AIModelPerformance).where(
            AIModelPerformance.performance_date >= cutoff_date.date()
        ).order_by(desc(AIModelPerformance.performance_date))
        
        result = await db.execute(query)
        performance_data = result.scalars().all()
        
        # Calculate aggregate statistics
        if performance_data:
            avg_accuracy = sum(float(p.accuracy_percentage) for p in performance_data) / len(performance_data)
            avg_user_satisfaction = sum(float(p.average_user_satisfaction or 0) for p in performance_data) / len(performance_data)
            total_predictions = sum(p.total_predictions for p in performance_data)
        else:
            avg_accuracy = 0.0
            avg_user_satisfaction = 0.0
            total_predictions = 0
        
        return {
            "average_accuracy": round(avg_accuracy, 2),
            "average_user_satisfaction": round(avg_user_satisfaction, 2),
            "total_predictions": total_predictions,
            "performance_trend": "improving" if len(performance_data) > 1 and 
                               performance_data[0].accuracy_percentage > performance_data[-1].accuracy_percentage 
                               else "stable",
            "daily_performance": [
                {
                    "date": p.performance_date.isoformat(),
                    "accuracy": float(p.accuracy_percentage),
                    "predictions": p.total_predictions
                }
                for p in performance_data
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting AI performance stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve performance statistics"
        )