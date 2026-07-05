"""
Admin Economy Dashboard API routes
"""

from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from src.core.database import get_db
from src.auth.dependencies import require_admin
from src.models.user import User
from src.models.market_transaction import MarketPrice, MarketTransaction, EconomicMetrics, PriceAlert
from src.models.station import Station
from src.models.sector import Sector
from src.models.player import Player
from src.services.economy_analytics_service import EconomyAnalyticsService, InterventionError


router = APIRouter(prefix="/admin/economy", tags=["admin-economy"])


# Request/Response models
class MarketInterventionRequest(BaseModel):
    intervention_type: str = Field(
        ...,
        description=(
            "Type of intervention: price_adjustment, inject_liquidity, "
            "reset_market (freeze_trading is off-canon; returns 501)"
        ),
    )
    parameters: dict = Field(..., description="Intervention-specific parameters")


class MarketDataItem(BaseModel):
    station_id: str
    port_name: str
    sector_name: str
    commodity: str
    buy_price: int
    sell_price: int
    quantity: int
    last_updated: str


class EconomicMetricsResponse(BaseModel):
    total_trade_volume: int
    total_credits_in_circulation: int
    average_profit_margin: float
    most_traded_commodity: str
    economic_health_score: float


class PriceAlertResponse(BaseModel):
    id: str
    timestamp: str
    alert_type: str
    severity: str
    station_id: Optional[str] = None
    port_name: Optional[str] = None
    sector_id: Optional[str] = None
    resource_type: Optional[str] = None
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    description: Optional[str] = None
    recommended_action: str

    class Config:
        extra = "allow"  # Allow additional fields


class InterventionResponse(BaseModel):
    intervention_id: str
    type: str
    status: str
    timestamp: str
    result: dict
    message: str


class PriceAlertCreateRequest(BaseModel):
    station_id: str = Field(..., description="Station UUID the alert monitors")
    commodity: str = Field(..., min_length=1, max_length=50)
    alert_type: str = Field(..., description="price_spike, price_drop, high_volume, low_supply")
    threshold_value: float


@router.get("/market-data", response_model=List[MarketDataItem])
async def get_market_data(
    commodity_filter: Optional[str] = Query(None, description="Filter by commodity type"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of results"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get current market prices across all ports for the admin economy dashboard.

    Returns a flat list of market data items with station, commodity, and pricing info.

    **Required permissions**: Admin access
    """
    try:
        # Query market prices with station and sector information
        query = (
            db.query(
                MarketPrice.station_id,
                MarketPrice.commodity,
                MarketPrice.buy_price,
                MarketPrice.sell_price,
                MarketPrice.quantity,
                MarketPrice.updated_at,
                Station.name.label('port_name'),
                Station.sector_id,
                Sector.name.label('sector_name'),
            )
            .join(Station, MarketPrice.station_id == Station.id)
            .outerjoin(Sector, Station.sector_uuid == Sector.id)
        )

        if commodity_filter:
            query = query.filter(MarketPrice.commodity == commodity_filter)

        results = query.limit(limit).all()

        market_data = []
        for row in results:
            market_data.append(MarketDataItem(
                station_id=str(row.station_id),
                port_name=row.port_name or "Unknown Port",
                sector_name=row.sector_name or "Unknown Sector",
                commodity=row.commodity,
                buy_price=row.buy_price,
                sell_price=row.sell_price,
                quantity=row.quantity,
                last_updated=row.updated_at.isoformat() if row.updated_at else datetime.utcnow().isoformat()
            ))

        return market_data
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve market data: {str(e)}"
        )


@router.get("/metrics", response_model=EconomicMetricsResponse)
async def get_economic_metrics(
    time_period: Optional[str] = Query("24h", description="Time period for metrics"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get key economic health metrics.

    Returns summary metrics including trade volume, credits in circulation,
    profit margins, and economic health score.

    **Required permissions**: Admin access
    """
    try:
        # Try to get latest stored metrics first
        latest_metrics = db.query(EconomicMetrics).order_by(
            EconomicMetrics.date.desc()
        ).first()

        if latest_metrics:
            return EconomicMetricsResponse(
                total_trade_volume=latest_metrics.total_trade_volume or 0,
                total_credits_in_circulation=latest_metrics.total_credits_in_circulation or 0,
                average_profit_margin=latest_metrics.average_profit_margin or 0.0,
                most_traded_commodity=latest_metrics.most_traded_commodity or "None",
                economic_health_score=latest_metrics.economic_health_score * 100 if latest_metrics.economic_health_score else 50.0
            )
        else:
            # Calculate live metrics if no stored metrics exist.
            # (Ported from the retired legacy /admin/economy router so the
            # time-period-aware live computation is not lost.)
            now = datetime.utcnow()
            time_filters = {
                "24h": now - timedelta(hours=24),
                "7d": now - timedelta(days=7),
                "30d": now - timedelta(days=30)
            }
            time_threshold = time_filters.get(time_period or "24h", time_filters["24h"])

            total_credits = (
                db.query(func.sum(Player.credits))
                .filter(Player.is_active == True)
                .scalar() or 0
            )

            # Trade volume within the requested period
            trade_volume = (
                db.query(func.sum(MarketTransaction.total_value))
                .filter(MarketTransaction.timestamp >= time_threshold)
                .scalar() or 0
            )

            # Average profit margin within the requested period
            profit_margin_result = (
                db.query(func.avg(MarketTransaction.profit_margin))
                .filter(
                    and_(
                        MarketTransaction.timestamp >= time_threshold,
                        MarketTransaction.profit_margin.isnot(None)
                    )
                )
                .scalar()
            )
            average_profit_margin = float(profit_margin_result) if profit_margin_result else 0.0

            # Most traded commodity by quantity within the requested period
            most_traded = (
                db.query(
                    MarketTransaction.commodity,
                    func.sum(MarketTransaction.quantity).label('total_quantity')
                )
                .filter(MarketTransaction.timestamp >= time_threshold)
                .group_by(MarketTransaction.commodity)
                .order_by(desc('total_quantity'))
                .first()
            )

            # Economic health score derived from trade volume, market
            # activity, and profit margins (0-100 scale to match the
            # stored-metrics branch above).
            transactions_count = (
                db.query(MarketTransaction)
                .filter(MarketTransaction.timestamp >= time_threshold)
                .count()
            )
            volume_factor = min(1.0, int(trade_volume) / 1_000_000)  # Normalize to 1M credits
            activity_factor = min(1.0, transactions_count / 100)     # Normalize to 100 transactions
            margin_factor = min(1.0, max(0.0, average_profit_margin / 50.0))  # Normalize to 50% margin
            economic_health_score = ((volume_factor + activity_factor + margin_factor) / 3.0) * 100

            return EconomicMetricsResponse(
                total_trade_volume=int(trade_volume),
                total_credits_in_circulation=int(total_credits),
                average_profit_margin=average_profit_margin,
                most_traded_commodity=most_traded.commodity if most_traded else "None",
                economic_health_score=economic_health_score
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve economic metrics: {str(e)}"
        )


@router.get("/price-alerts", response_model=list[PriceAlertResponse])
async def get_price_alerts(
    threshold_percent: float = Query(10.0, description="Alert threshold percentage", ge=1.0, le=100.0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get price anomalies and market manipulation alerts.

    This endpoint monitors for:
    - Significant price spikes or crashes
    - Potential market manipulation patterns
    - Wash trading detection
    - Abnormal trading volumes

    Alerts are sorted by severity (critical, high, medium, low).

    **Required permissions**: Admin access
    """
    try:
        analytics_service = EconomyAnalyticsService(db)
        alerts = analytics_service.get_price_alerts(threshold_percent=threshold_percent)
        return [PriceAlertResponse(**alert) for alert in alerts]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve price alerts: {str(e)}"
        )


@router.post("/intervention", response_model=InterventionResponse)
async def perform_market_intervention(
    request: MarketInterventionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Perform market intervention actions.

    Available intervention types:

    1. **price_adjustment**: Adjust prices by percentage
       - Parameters: resource_type, adjustment_percent, port_ids (optional)

    2. **inject_liquidity**: Persist real stock into a station's market —
       writes station.commodities[commodity]["quantity"] (clamped to the
       commodity's capacity) plus the mirrored MarketPrice row, then
       reprices off the new stock. An unknown resource_type or a commodity
       the station doesn't stock is skipped, not silently accepted.
       - Parameters: station_id, resources (dict of resource_type: amount)

    3. **reset_market**: Reset prices to baseline values
       - Parameters: resource_type

    **freeze_trading** is NOT a supported intervention — it is off-canon
    (no trade path anywhere checks a freeze flag) and now returns
    **501 Not Implemented** rather than a canned success response.

    Every intervention that actually commits a state change is logged in
    the audit trail; a rejected or failed call (400/501/500) writes no
    audit row.

    **Required permissions**: Admin access
    """
    try:
        analytics_service = EconomyAnalyticsService(db)

        # Add admin ID to parameters for audit logging
        parameters = request.parameters.copy()
        parameters['admin_id'] = admin.id

        result = analytics_service.perform_market_intervention(
            intervention_type=request.intervention_type,
            parameters=parameters
        )

        return InterventionResponse(**result)
    except InterventionError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Market intervention failed: {str(e)}"
        )


@router.get("/dashboard-summary")
async def get_dashboard_summary(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get a comprehensive summary for the economy dashboard.

    Combines key metrics from all economy endpoints for a quick overview.

    **Required permissions**: Admin access
    """
    try:
        analytics_service = EconomyAnalyticsService(db)

        # Get all data
        market_data = analytics_service.get_market_data(timeframe="24h")
        metrics = analytics_service.get_economic_metrics()
        alerts = analytics_service.get_price_alerts(threshold_percent=10.0)

        # Count alerts by severity
        alert_counts = {
            "critical": len([a for a in alerts if a.get('severity') == 'critical']),
            "high": len([a for a in alerts if a.get('severity') == 'high']),
            "medium": len([a for a in alerts if a.get('severity') == 'medium']),
            "low": len([a for a in alerts if a.get('severity') == 'low'])
        }

        return {
            "timestamp": metrics['timestamp'],
            "health_score": metrics['health_score'],
            "daily_summary": {
                "total_transactions": market_data['summary']['total_transactions'],
                "total_volume": market_data['summary']['total_volume'],
                "total_value": market_data['summary']['total_value'],
                "unique_traders": market_data['summary']['unique_traders']
            },
            "key_metrics": {
                "gdp": metrics['economic_indicators']['gdp'],
                "money_supply": metrics['economic_indicators']['money_supply'],
                "market_velocity": metrics['market_velocity'],
                "gini_coefficient": metrics['wealth_distribution']['gini_coefficient']
            },
            "alert_summary": {
                "total_alerts": len(alerts),
                "by_severity": alert_counts,
                "critical_alerts": [a for a in alerts if a.get('severity') == 'critical'][:3]
            },
            "top_trading_ports": market_data['top_trading_ports'][:5]
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate dashboard summary: {str(e)}"
        )


# ---------------------------------------------------------------------------
# DB-backed price alert management (ported from the retired legacy
# /admin/economy router so the still-working capability is not lost).
# Note: GET /price-alerts above is analytics-derived anomaly detection;
# these two endpoints manage persistent PriceAlert rows.
# ---------------------------------------------------------------------------


@router.post("/create-alert")
async def create_price_alert(
    request: PriceAlertCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Create a new persistent price monitoring alert.

    **Required permissions**: Admin access
    """
    station = db.query(Station).filter(Station.id == request.station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Capture the current market value for the monitored commodity so the
    # alert has a real baseline (the model requires current_value).
    market_price = db.query(MarketPrice).filter(
        and_(
            MarketPrice.station_id == station.id,
            MarketPrice.commodity == request.commodity
        )
    ).first()
    if not market_price:
        raise HTTPException(
            status_code=404,
            detail=f"No market price record for commodity '{request.commodity}' at this station"
        )

    alert = PriceAlert(
        station_id=station.id,
        commodity=request.commodity,
        alert_type=request.alert_type,
        threshold_value=request.threshold_value,
        current_value=float(market_price.sell_price),
        message=(
            f"Admin alert: monitor {request.commodity} at {station.name} "
            f"for {request.alert_type} (threshold {request.threshold_value})"
        ),
        is_active=True
    )

    db.add(alert)
    db.commit()
    db.refresh(alert)

    return {"message": "Price alert created successfully", "alert_id": str(alert.id)}


@router.delete("/alerts/{alert_id}")
async def delete_price_alert(
    alert_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Delete a persistent price alert.

    **Required permissions**: Admin access
    """
    alert = db.query(PriceAlert).filter(PriceAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Price alert not found")

    db.delete(alert)
    db.commit()

    return {"message": "Price alert deleted successfully", "alert_id": str(alert_id)}
