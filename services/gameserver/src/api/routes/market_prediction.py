"""
Market Prediction API endpoints.

Player-facing read surface over the live :class:`MarketPredictionEngine` — a
pure-statistical engine (moving averages, std-dev bands, linear trend) that
reads REAL market data (PriceHistory snapshots → MarketPrice current/previous →
live Station commodity prices). It does NOT read the empty ``AIMarketPrediction``
table.

Endpoints (all require an authenticated player):

* ``GET /market-prediction/predict``        — single-commodity price prediction.
* ``GET /market-prediction/predict/all``    — predictions for every core commodity at a station.
* ``GET /market-prediction/opportunities``  — galaxy-wide buy-low / sell-high opportunities.
* ``GET /market-prediction/analysis``       — comprehensive market analysis for one commodity.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_async_session
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.market_prediction_engine import (
    MarketPredictionEngine,
    PricePrediction as EnginePricePrediction,
    TradeOpportunity as EngineTradeOpportunity,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market-prediction", tags=["market"])

# The engine is stateless (no per-request state) — instantiate once and reuse,
# mirroring how the AI/trading services hold a single MarketPredictionEngine().
_engine = MarketPredictionEngine()


# ---------------------------------------------------------------------------
# Response models — mirror the engine dataclasses' to_dict() shape
# ---------------------------------------------------------------------------


class PricePredictionResponse(BaseModel):
    """A single commodity price prediction."""

    commodity: str
    station_id: str
    current_price: float
    predicted_price: float
    price_change_pct: float
    trend: str = Field(..., description="rising, falling, stable, or unknown")
    confidence: float = Field(..., description="0.0 to 1.0")
    volatility: float
    lower_bound: float
    upper_bound: float
    prediction_horizon_hours: int
    factors: List[str]
    timestamp: str

    @classmethod
    def from_engine(cls, p: EnginePricePrediction) -> "PricePredictionResponse":
        return cls(**p.to_dict())


class TradeOpportunityResponse(BaseModel):
    """A buy-low / sell-high opportunity identified by the prediction engine."""

    commodity: str
    buy_station_id: str
    buy_sector_id: int
    buy_price: float
    sell_station_id: str
    sell_sector_id: int
    sell_price: float
    profit_per_unit: float
    confidence: float
    reasoning: str

    @classmethod
    def from_engine(cls, o: EngineTradeOpportunity) -> "TradeOpportunityResponse":
        return cls(**o.to_dict())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/predict", response_model=PricePredictionResponse)
async def predict_price(
    commodity: str = Query(..., description="Commodity name, e.g. 'ore', 'fuel'"),
    station_id: Optional[str] = Query(
        None, description="Restrict to a station; omit for a global prediction"
    ),
    hours_ahead: int = Query(
        24, ge=1, le=168, description="Prediction horizon in hours (max 1 week)"
    ),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """Predict the future price of a single commodity (optionally at a station)."""
    prediction = await _engine.predict_prices(
        db, commodity=commodity, station_id=station_id, hours_ahead=hours_ahead
    )
    if prediction is None:
        raise HTTPException(
            status_code=503,
            detail="Market prediction is temporarily unavailable",
        )
    return PricePredictionResponse.from_engine(prediction)


@router.get("/predict/all", response_model=List[PricePredictionResponse])
async def predict_all_prices(
    station_id: Optional[str] = Query(
        None, description="Restrict to a station; omit for global predictions"
    ),
    hours_ahead: int = Query(
        24, ge=1, le=168, description="Prediction horizon in hours (max 1 week)"
    ),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """Predict prices for every core commodity (optionally at a single station)."""
    predictions = await _engine.batch_predict(
        db, station_id=station_id, hours_ahead=hours_ahead
    )
    return [PricePredictionResponse.from_engine(p) for p in predictions.values()]


@router.get("/opportunities", response_model=List[TradeOpportunityResponse])
async def find_opportunities(
    min_profit_margin: float = Query(
        0.10,
        ge=0.0,
        le=10.0,
        description="Minimum profit margin (fraction, e.g. 0.10 = 10%)",
    ),
    limit: int = Query(10, ge=1, le=50, description="Maximum opportunities to return"),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """Scan the galaxy for buy-low / sell-high trade opportunities."""
    opportunities = await _engine.find_opportunities(
        db, min_profit_margin=min_profit_margin, limit=limit
    )
    return [TradeOpportunityResponse.from_engine(o) for o in opportunities]


@router.get("/analysis")
async def commodity_analysis(
    commodity: str = Query(..., description="Commodity name, e.g. 'ore', 'fuel'"),
    station_id: Optional[str] = Query(
        None, description="Restrict to a station; omit for a global analysis"
    ),
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_session),
):
    """Return a comprehensive market analysis for a single commodity."""
    return await _engine.get_commodity_analysis(
        db, commodity=commodity, station_id=station_id
    )
