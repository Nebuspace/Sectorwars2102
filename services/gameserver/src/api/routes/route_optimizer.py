"""
Route Optimizer API Routes

Exposes the graph-based route optimization engine
(``src.services.route_optimizer.RouteOptimizer``) to players, letting them
request an optimal route from a start sector under a chosen objective:
``shortest`` (fewest warps to an end sector), ``profit``, ``risk`` or
``balanced`` (greedy trading routes maximising the chosen dimension).
"""

import logging
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from src.core.database import get_async_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.route_optimizer import (
    RouteOptimizer,
    RouteObjective,
    OptimizedRoute,
)

router = APIRouter(prefix="/routes", tags=["routes"])
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------

# Player-facing objective keyword -> internal RouteObjective.
# "shortest" is handled separately (direct A->B via find_shortest_path).
_OBJECTIVE_MAP = {
    "profit": RouteObjective.MAX_PROFIT,
    "risk": RouteObjective.MIN_RISK,
    "balanced": RouteObjective.BALANCED,
}
_VALID_OBJECTIVES = ["shortest", "profit", "risk", "balanced"]


class RouteOptimizeRequest(BaseModel):
    start_sector_id: str = Field(
        ..., description="Sector number or UUID to start the route from"
    )
    end_sector_id: Optional[str] = Field(
        None,
        description=(
            "Target sector (number or UUID). Required for the 'shortest' "
            "objective; ignored for trading objectives."
        ),
    )
    objective: str = Field(
        "balanced",
        description="One of: shortest, profit, risk, balanced",
    )
    cargo_capacity: int = Field(
        100,
        gt=0,
        le=100000,
        description="Units the player can carry (used by trading objectives)",
    )
    max_route_time: float = Field(
        24.0,
        gt=0.0,
        le=168.0,
        description="Maximum route time in hours (trading objectives)",
    )
    risk_tolerance: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="0.0 (safe) to 1.0 (risky)",
    )


class OpportunityResponse(BaseModel):
    from_sector: str
    to_sector: str
    commodity: str
    buy_price: float
    sell_price: float
    profit_per_unit: float
    max_quantity: int
    distance: int
    travel_time_hours: float
    risk_factor: float
    confidence: float


class RouteOptimizeResponse(BaseModel):
    objective: str
    route_type: str
    sectors: List[str]
    total_profit: float
    total_distance: int
    total_time_hours: float
    total_risk: float
    cargo_efficiency: float
    profit_per_hour: float
    route_confidence: float
    opportunities: List[OpportunityResponse]


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.post("/optimize", response_model=RouteOptimizeResponse)
async def optimize_route(
    request: RouteOptimizeRequest,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Compute an optimized route from ``start_sector_id``.

    Objectives:
      * ``shortest`` - fewest warps from start to ``end_sector_id``.
      * ``profit``   - greedy trading route maximising total profit.
      * ``risk``     - profitable route minimising hazard exposure.
      * ``balanced`` - weighs profit, time and risk.
    """
    objective = request.objective.strip().lower()
    if objective not in _VALID_OBJECTIVES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid objective '{request.objective}'. "
                f"Must be one of: {', '.join(_VALID_OBJECTIVES)}"
            ),
        )

    optimizer = RouteOptimizer()

    try:
        if objective == "shortest":
            if not request.end_sector_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="end_sector_id is required for the 'shortest' objective",
                )

            path = await optimizer.find_shortest_path(
                db,
                request.start_sector_id,
                request.end_sector_id,
            )
            if not path:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"No route found from sector {request.start_sector_id} "
                        f"to sector {request.end_sector_id}"
                    ),
                )

            sectors = [str(sid) for sid in path]
            return RouteOptimizeResponse(
                objective=objective,
                route_type="direct" if len(sectors) < 3 else "linear",
                sectors=sectors,
                total_profit=0.0,
                total_distance=max(0, len(sectors) - 1),
                total_time_hours=0.0,
                total_risk=0.0,
                cargo_efficiency=0.0,
                profit_per_hour=0.0,
                route_confidence=1.0,
                opportunities=[],
            )

        # Trading objectives: profit / risk / balanced
        route: Optional[OptimizedRoute] = await optimizer.find_optimal_route(
            db,
            start_sector_id=request.start_sector_id,
            player_id=str(current_player.id),
            cargo_capacity=request.cargo_capacity,
            max_route_time=request.max_route_time,
            objective=_OBJECTIVE_MAP[objective],
            risk_tolerance=request.risk_tolerance,
        )

        if route is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No viable {objective} route found from sector "
                    f"{request.start_sector_id}"
                ),
            )

        return RouteOptimizeResponse(
            objective=objective,
            route_type=route.route_type,
            sectors=route.sectors,
            total_profit=route.total_profit,
            total_distance=route.total_distance,
            total_time_hours=route.total_time_hours,
            total_risk=route.total_risk,
            cargo_efficiency=route.cargo_efficiency,
            profit_per_hour=route.profit_per_hour,
            route_confidence=route.route_confidence,
            opportunities=[
                OpportunityResponse(
                    from_sector=o.from_sector_id,
                    to_sector=o.to_sector_id,
                    commodity=o.commodity_id,
                    buy_price=o.buy_price,
                    sell_price=o.sell_price,
                    profit_per_unit=o.profit_per_unit,
                    max_quantity=o.max_quantity,
                    distance=o.distance,
                    travel_time_hours=o.travel_time_hours,
                    risk_factor=o.risk_factor,
                    confidence=o.confidence,
                )
                for o in route.opportunities
            ],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error optimizing route for player {current_player.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to optimize route",
        )
