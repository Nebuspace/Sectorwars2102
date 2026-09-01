"""
Admin Economy Dashboard API routes
"""

import logging
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from src.core.database import get_db
from src.auth.admin_scopes import ECONOMY_INTERVENE, PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.models.market_transaction import MarketPrice, MarketTransaction, EconomicMetrics, PriceAlert
from src.models.station import Station
from src.models.sector import Sector
from src.models.player import Player
from src.services.economy_analytics_service import EconomyAnalyticsService, InterventionError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/economy", tags=["admin-economy"])


# Request/Response models
class MarketInterventionRequest(BaseModel):
    intervention_type: str = Field(
        ...,
        description=(
            "Type of intervention: price_adjustment, inject_liquidity, "
            "reset_market"
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
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
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
        logger.error("Failed to retrieve market data: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve market data")


@router.get("/metrics", response_model=EconomicMetricsResponse)
async def get_economic_metrics(
    time_period: Optional[str] = Query("24h", description="Time period for metrics"),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
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
        logger.error("Failed to retrieve economic metrics: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve economic metrics")


@router.get("/price-alerts", response_model=list[PriceAlertResponse])
async def get_price_alerts(
    threshold_percent: float = Query(10.0, description="Alert threshold percentage", ge=1.0, le=100.0),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
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
        logger.error("Failed to retrieve price alerts: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve price alerts")


@router.post("/intervention", response_model=InterventionResponse)
async def perform_market_intervention(
    request: MarketInterventionRequest,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
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

    Every intervention that actually commits a state change is logged in
    the audit trail; a rejected or failed call (400/500) writes no
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
        logger.error("Market intervention failed: %s", e)
        raise HTTPException(status_code=500, detail="Market intervention failed")


@router.get("/dashboard-summary")
async def get_dashboard_summary(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
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
        logger.error("Failed to generate dashboard summary: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate dashboard summary")


# ---------------------------------------------------------------------------
# DB-backed price alert management (ported from the retired legacy
# /admin/economy router so the still-working capability is not lost).
# Note: GET /price-alerts above is analytics-derived anomaly detection;
# these two endpoints manage persistent PriceAlert rows.
# ---------------------------------------------------------------------------


@router.post("/create-alert")
async def create_price_alert(
    request: PriceAlertCreateRequest,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
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
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
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


# ---------------------------------------------------------------------------
# Economy Levers panel (lifecycle.md § Balancing levers) — cycle-50
# ---------------------------------------------------------------------------


class RegionLeverPatch(BaseModel):
    tax_rate: Optional[float] = Field(None, ge=0.05, le=0.25)
    starting_credits: Optional[int] = Field(None, ge=100, le=10000)


class ShipSpecLeverPatch(BaseModel):
    base_cost: int = Field(..., ge=1, le=50_000_000)


class UpgradeLeverPatch(BaseModel):
    base_cost: Optional[int] = Field(None, ge=1, le=50_000_000)
    cost_multiplier: Optional[float] = Field(None, ge=1.0, le=10.0)


class BountyPayoutLeverPatch(BaseModel):
    bounty_payout_ratio: float = Field(..., ge=0.0, le=5.0)


class InsuranceLeverPatch(BaseModel):
    insurance_premium_pct: Optional[dict[str, float]] = None
    insurance_net_payout_pct: Optional[dict[str, float]] = None


class StationCommodityLeverPatch(BaseModel):
    base_price: Optional[int] = Field(None, ge=0, le=50_000_000)
    production_rate: Optional[float] = Field(None, ge=0.0, le=1_000_000.0)


@router.get("/levers")
async def get_economy_levers(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """Snapshot of admin-editable balancing levers (regions, ship costs, upgrades,
    bounty/insurance ratios, per-station commodity base_price/production_rate)."""
    from src.models.region import Region
    from src.models.ship import ShipSpecification
    from src.services.economy_balancing_levers import snapshot as levers_snapshot
    from src.services.ship_upgrade_service import ShipUpgradeService

    regions = db.query(Region).order_by(Region.name).all()
    specs = db.query(ShipSpecification).order_by(ShipSpecification.base_cost).all()
    upgrades = []
    for utype, definition in ShipUpgradeService.UPGRADE_DEFINITIONS.items():
        upgrades.append({
            "type": utype.value if hasattr(utype, "value") else str(utype),
            "base_cost": int(definition["base_cost"]),
            "cost_multiplier": float(definition["cost_multiplier"]),
            "description": definition.get("description", ""),
        })

    stations = db.query(Station).order_by(Station.name).limit(500).all()
    station_commodities = []
    for station in stations:
        commodities = station.commodities or {}
        if not isinstance(commodities, dict):
            continue
        for commodity_key, raw in commodities.items():
            if not isinstance(raw, dict):
                continue
            station_commodities.append({
                "station_id": str(station.id),
                "station_name": station.name,
                "commodity": str(commodity_key),
                "base_price": int(raw.get("base_price") or 0),
                "production_rate": float(raw.get("production_rate") or 0),
            })

    ratios = levers_snapshot()
    return {
        "regions": [
            {
                "id": str(r.id),
                "name": r.name,
                "display_name": r.display_name,
                "tax_rate": float(r.tax_rate),
                "starting_credits": int(r.starting_credits),
                "status": r.status,
            }
            for r in regions
        ],
        "ship_specs": [
            {
                "type": spec.type.value if hasattr(spec.type, "value") else str(spec.type),
                "base_cost": int(spec.base_cost),
                "is_npc_only": bool(spec.is_npc_only),
            }
            for spec in specs
        ],
        "upgrades": upgrades,
        "bounty_payout_ratio": ratios["bounty_payout_ratio"],
        "insurance_premium_pct": ratios["insurance_premium_pct"],
        "insurance_net_payout_pct": ratios["insurance_net_payout_pct"],
        "station_commodities": station_commodities,
    }


@router.patch("/levers/regions/{region_id}")
async def patch_region_levers(
    region_id: UUID,
    body: RegionLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
    db: Session = Depends(get_db),
):
    """Adjust region tax_rate (5–25%) and/or starting_credits."""
    from src.models.region import Region

    region = db.query(Region).filter(Region.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    if body.tax_rate is None and body.starting_credits is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    applied = {}
    if body.tax_rate is not None:
        applied["tax_rate"] = {"old": float(region.tax_rate), "new": float(body.tax_rate)}
        region.tax_rate = float(body.tax_rate)
    if body.starting_credits is not None:
        applied["starting_credits"] = {
            "old": int(region.starting_credits),
            "new": int(body.starting_credits),
        }
        region.starting_credits = int(body.starting_credits)
    db.commit()
    return {
        "region_id": str(region.id),
        "applied": applied,
        "tax_rate": float(region.tax_rate),
        "starting_credits": int(region.starting_credits),
    }


@router.patch("/levers/ship-specs/{ship_type}")
async def patch_ship_spec_lever(
    ship_type: str,
    body: ShipSpecLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
    db: Session = Depends(get_db),
):
    """Adjust ShipSpecification.base_cost (direct sink throttle)."""
    from src.models.ship import ShipSpecification, ShipType

    try:
        st = ShipType(ship_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown ship type: {ship_type}")

    spec = db.query(ShipSpecification).filter(ShipSpecification.type == st).first()
    if not spec:
        raise HTTPException(status_code=404, detail="Ship specification not found")

    old = int(spec.base_cost)
    spec.base_cost = int(body.base_cost)
    db.commit()
    return {
        "type": st.value,
        "base_cost": {"old": old, "new": int(spec.base_cost)},
    }


@router.patch("/levers/upgrades/{upgrade_type}")
async def patch_upgrade_lever(
    upgrade_type: str,
    body: UpgradeLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
):
    """Adjust in-process UPGRADE_DEFINITIONS costs (process-local until restart)."""
    from src.models.ship import UpgradeType
    from src.services.ship_upgrade_service import ShipUpgradeService

    try:
        ut = UpgradeType(upgrade_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown upgrade type: {upgrade_type}")

    if ut not in ShipUpgradeService.UPGRADE_DEFINITIONS:
        raise HTTPException(status_code=404, detail="Upgrade definition not found")
    if body.base_cost is None and body.cost_multiplier is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Copy-on-write so we don't mutate shared nested refs unexpectedly.
    current = dict(ShipUpgradeService.UPGRADE_DEFINITIONS[ut])
    applied = {}
    if body.base_cost is not None:
        applied["base_cost"] = {"old": int(current["base_cost"]), "new": int(body.base_cost)}
        current["base_cost"] = int(body.base_cost)
    if body.cost_multiplier is not None:
        applied["cost_multiplier"] = {
            "old": float(current["cost_multiplier"]),
            "new": float(body.cost_multiplier),
        }
        current["cost_multiplier"] = float(body.cost_multiplier)
    ShipUpgradeService.UPGRADE_DEFINITIONS[ut] = current
    return {
        "type": ut.value,
        "applied": applied,
        "note": "In-process override; reverts on gameserver restart unless persisted elsewhere.",
        "actor": str(admin.id),
    }


@router.patch("/levers/bounty-payout")
async def patch_bounty_payout_lever(
    body: BountyPayoutLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
):
    """Adjust in-process bounty payout faucet ratio (lifecycle.md balancing levers)."""
    from src.services.economy_balancing_levers import set_bounty_payout_ratio

    try:
        applied = set_bounty_payout_ratio(body.bounty_payout_ratio)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "bounty_payout_ratio": applied,
        "note": "In-process override; reverts on gameserver restart.",
        "actor": str(admin.id),
    }


@router.patch("/levers/insurance")
async def patch_insurance_levers(
    body: InsuranceLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
):
    """Adjust in-process insurance premium / net-payout ratios by tier."""
    from src.services.economy_balancing_levers import (
        set_insurance_net_payout_pct,
        set_insurance_premium_pct,
        snapshot as levers_snapshot,
    )

    if body.insurance_premium_pct is None and body.insurance_net_payout_pct is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    applied: dict = {}
    try:
        if body.insurance_premium_pct is not None:
            applied["insurance_premium_pct"] = set_insurance_premium_pct(
                body.insurance_premium_pct
            )
        if body.insurance_net_payout_pct is not None:
            applied["insurance_net_payout_pct"] = set_insurance_net_payout_pct(
                body.insurance_net_payout_pct
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snap = levers_snapshot()
    return {
        "applied": applied,
        "insurance_premium_pct": snap["insurance_premium_pct"],
        "insurance_net_payout_pct": snap["insurance_net_payout_pct"],
        "note": "In-process override; reverts on gameserver restart.",
        "actor": str(admin.id),
    }


@router.patch("/levers/stations/{station_id}/commodities/{commodity}")
async def patch_station_commodity_lever(
    station_id: UUID,
    commodity: str,
    body: StationCommodityLeverPatch,
    admin: User = Depends(require_scope(ECONOMY_INTERVENE)),
    db: Session = Depends(get_db),
):
    """Persist per-station commodity base_price and/or production_rate."""
    from sqlalchemy.orm.attributes import flag_modified

    if body.base_price is None and body.production_rate is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    commodities = dict(station.commodities or {})
    entry = commodities.get(commodity)
    if not isinstance(entry, dict):
        raise HTTPException(
            status_code=404,
            detail=f"Commodity '{commodity}' not stocked at this station",
        )

    applied: dict = {}
    updated = dict(entry)
    if body.base_price is not None:
        applied["base_price"] = {
            "old": int(updated.get("base_price") or 0),
            "new": int(body.base_price),
        }
        updated["base_price"] = int(body.base_price)
    if body.production_rate is not None:
        applied["production_rate"] = {
            "old": float(updated.get("production_rate") or 0),
            "new": float(body.production_rate),
        }
        updated["production_rate"] = float(body.production_rate)

    commodities[commodity] = updated
    station.commodities = commodities
    flag_modified(station, "commodities")
    db.commit()

    return {
        "station_id": str(station.id),
        "commodity": commodity,
        "applied": applied,
        "base_price": int(updated.get("base_price") or 0),
        "production_rate": float(updated.get("production_rate") or 0),
        "actor": str(admin.id),
    }
