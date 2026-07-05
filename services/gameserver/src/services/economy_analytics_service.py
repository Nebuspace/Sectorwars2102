"""
Economy Analytics Service for Admin Dashboard
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm.attributes import flag_modified

from src.core.commodity_economy import (
    COMMODITY_BASE_PRICES,
    base_price as commodity_base_price,
    canonical_commodity,
)
from src.models.market_transaction import MarketTransaction, MarketPrice, PriceHistory, EconomicMetrics
from src.models.station import Station
from src.models.player import Player
from src.services.audit_service import AuditService, AuditAction
from src.services.trading_service import TradingService


class InterventionError(Exception):
    """Raised when a market intervention cannot honestly complete; carries
    an HTTP status hint for the route (mirrors ConstructionError in
    construction_service.py — same carry-a-status-code-from-service-to-route
    convention)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class EconomyAnalyticsService:
    def __init__(self, db: Session):
        self.db = db
        self.audit_service = AuditService(db)

    def get_market_data(self, timeframe: str = "24h",
                       resource_type: Optional[str] = None,
                       sector_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """Get comprehensive market data for admin dashboard"""
        # Parse timeframe
        hours = self._parse_timeframe(timeframe)
        start_time = datetime.utcnow() - timedelta(hours=hours)

        # Build query for transactions
        query = self.db.query(MarketTransaction).filter(
            MarketTransaction.timestamp >= start_time
        )

        if resource_type:
            query = query.filter(MarketTransaction.commodity == resource_type)

        if sector_id:
            query = query.join(Station, MarketTransaction.station_id == Station.id).filter(Station.sector_id == sector_id)

        transactions = query.all()

        # Calculate market statistics
        total_volume = sum(t.quantity for t in transactions)
        total_value = sum(t.total_value for t in transactions)
        unique_traders = len(set(t.player_id for t in transactions if t.player_id))

        # Get price trends
        price_data = self._get_price_trends(start_time, resource_type, sector_id)

        # Get top trading stations
        top_ports = self._get_top_trading_ports(start_time, limit=10)

        # Get resource distribution
        resource_distribution = self._get_resource_distribution(transactions)

        return {
            "timeframe": timeframe,
            "start_time": start_time.isoformat(),
            "end_time": datetime.utcnow().isoformat(),
            "summary": {
                "total_transactions": len(transactions),
                "total_volume": total_volume,
                "total_value": float(total_value),
                "unique_traders": unique_traders,
                "average_transaction_value": float(total_value / len(transactions)) if transactions else 0
            },
            "price_trends": price_data,
            "top_trading_ports": top_ports,
            "resource_distribution": resource_distribution,
            "filters_applied": {
                "resource_type": resource_type,
                "sector_id": str(sector_id) if sector_id else None
            }
        }

    def get_economic_metrics(self) -> Dict[str, Any]:
        """Get key economic health metrics"""
        # Get latest economic metrics
        latest_metrics = self.db.query(EconomicMetrics).order_by(
            EconomicMetrics.date.desc()
        ).first()

        # Calculate inflation rates
        inflation_data = self._calculate_inflation_rates()

        # Get market liquidity
        liquidity_data = self._calculate_market_liquidity()

        # Get wealth distribution
        wealth_distribution = self._calculate_wealth_distribution()

        # Market velocity (turnover rate)
        velocity = self._calculate_market_velocity()

        # Economic indicators
        indicators = {
            "gdp": self._calculate_gdp(),
            "money_supply": self._calculate_money_supply(),
            "average_prices": self._get_average_prices(),
            "price_volatility": self._calculate_price_volatility()
        }

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "latest_metrics": {
                "total_credits": float(latest_metrics.total_credits_in_circulation) if latest_metrics else 0,
                "total_resources": float(latest_metrics.total_trade_volume) if latest_metrics else 0,
                "active_traders": latest_metrics.total_players_trading if latest_metrics else 0,
                "market_liquidity": float(latest_metrics.credit_velocity) if latest_metrics else 0
            },
            "inflation": inflation_data,
            "liquidity": liquidity_data,
            "wealth_distribution": wealth_distribution,
            "market_velocity": velocity,
            "economic_indicators": indicators,
            "health_score": self._calculate_health_score(indicators, velocity, wealth_distribution)
        }

    def get_price_alerts(self, threshold_percent: float = 10.0) -> List[Dict[str, Any]]:
        """Get price anomalies and alerts"""
        alerts = []

        try:
            # Get recent price changes by comparing current prices to previous prices
            prices = self.db.query(MarketPrice).all()

            for price in prices:
                if price.previous_buy_price and price.previous_buy_price > 0:
                    price_change = ((price.buy_price - price.previous_buy_price) / price.previous_buy_price) * 100

                    if abs(price_change) >= threshold_percent:
                        station = self.db.query(Station).filter(Station.id == price.station_id).first()
                        alerts.append({
                            "id": str(uuid.uuid4()),
                            "timestamp": price.updated_at.isoformat() if price.updated_at else datetime.utcnow().isoformat(),
                            "alert_type": "price_spike" if price_change > 0 else "price_crash",
                            "severity": self._calculate_alert_severity(price_change),
                            "station_id": str(price.station_id),
                            "port_name": station.name if station else "Unknown",
                            "sector_id": str(station.sector_id) if station else None,
                            "resource_type": price.commodity,
                            "previous_price": float(price.previous_buy_price),
                            "current_price": float(price.buy_price),
                            "price_change_percent": round(price_change, 2),
                            "recommended_action": self._get_recommended_action(price_change, price.commodity)
                        })
        except Exception as e:
            # If price alert detection fails, return empty list rather than crashing
            pass

        # Check for market manipulation
        try:
            manipulation_alerts = self._detect_market_manipulation()
            alerts.extend(manipulation_alerts)
        except Exception:
            pass

        # Sort by severity and timestamp
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        alerts.sort(key=lambda x: (severity_order.get(x.get('severity', 'low'), 3), x.get('timestamp', '')), reverse=True)

        return alerts

    def perform_market_intervention(self, intervention_type: str,
                                  parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Perform market intervention actions.

        The INTERVENTION audit row is written ONLY once the dispatched
        action returns without raising — i.e. only after a real, committed
        state change. Any raised exception (unknown type, invalid input,
        freeze_trading's honest 501, or a genuine DB error) rolls back and
        writes NO audit row (WO-ADM-ECON-TRUTH: previously every outcome,
        success OR failure, logged an unconditional INTERVENTION row,
        corrupting the audit trail with phantom entries for actions that
        changed nothing)."""
        intervention_id = uuid.uuid4()

        try:
            if intervention_type == "price_adjustment":
                result = self._adjust_prices(parameters)
            elif intervention_type == "inject_liquidity":
                result = self._inject_liquidity(parameters)
            elif intervention_type == "freeze_trading":
                result = self._freeze_trading(parameters)
            elif intervention_type == "reset_market":
                result = self._reset_market_prices(parameters)
            else:
                raise ValueError(f"Unknown intervention type: {intervention_type}")

            # Log the intervention
            self.audit_service.log_action(
                user_id=parameters.get('admin_id'),
                action=AuditAction.INTERVENTION,
                resource_type="economy",
                resource_id=str(intervention_id),
                details={
                    "intervention_type": intervention_type,
                    "parameters": {k: str(v) for k, v in parameters.items()},
                    "result": result
                }
            )

            self.db.commit()

            return {
                "intervention_id": str(intervention_id),
                "type": intervention_type,
                "status": "success",
                "timestamp": datetime.utcnow().isoformat(),
                "result": result,
                "message": f"Market intervention '{intervention_type}' completed successfully"
            }

        except Exception:
            # No audit row: a rejected/failed intervention never committed a
            # state change, so it must not appear as one in the trail.
            self.db.rollback()
            raise

    # Helper methods

    def _parse_timeframe(self, timeframe: str) -> int:
        """Parse timeframe string to hours"""
        if timeframe.endswith('h'):
            return int(timeframe[:-1])
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 24
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 24 * 7
        else:
            return 24  # Default to 24 hours

    def _get_price_trends(self, start_time: datetime,
                         resource_type: Optional[str],
                         sector_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
        """Get price trend data for charts"""
        query = self.db.query(PriceHistory).filter(
            PriceHistory.snapshot_date >= start_time
        )

        if resource_type:
            query = query.filter(PriceHistory.commodity == resource_type)

        if sector_id:
            query = query.join(Station, PriceHistory.station_id == Station.id).filter(Station.sector_id == sector_id)

        # Aggregate by hour
        trends = []
        history = query.order_by(PriceHistory.snapshot_date).all()

        if history:
            # Group by hour and calculate averages
            hourly_data = {}
            for record in history:
                hour_key = record.snapshot_date.replace(minute=0, second=0, microsecond=0)
                if hour_key not in hourly_data:
                    hourly_data[hour_key] = []
                # Use average of buy and sell price
                avg_price = (record.buy_price + record.sell_price) / 2.0
                hourly_data[hour_key].append(float(avg_price))

            for hour, prices in sorted(hourly_data.items()):
                trends.append({
                    "timestamp": hour.isoformat(),
                    "average_price": sum(prices) / len(prices),
                    "min_price": min(prices),
                    "max_price": max(prices),
                    "transaction_count": len(prices)
                })

        return trends

    def _get_top_trading_ports(self, start_time: datetime, limit: int = 10) -> List[Dict[str, Any]]:
        """Get ports with highest trading volume"""
        try:
            results = (
                self.db.query(
                    Station.id,
                    Station.name,
                    Station.sector_id,
                    func.count(MarketTransaction.id).label('transaction_count'),
                    func.sum(MarketTransaction.quantity).label('total_volume'),
                    func.sum(MarketTransaction.total_value).label('total_value')
                )
                .join(MarketTransaction, MarketTransaction.station_id == Station.id)
                .filter(MarketTransaction.timestamp >= start_time)
                .group_by(Station.id, Station.name, Station.sector_id)
                .order_by(desc('total_value'))
                .limit(limit)
                .all()
            )

            return [{
                "station_id": str(result.id),
                "station_name": result.name,
                "sector_id": str(result.sector_id) if result.sector_id else None,
                "transaction_count": result.transaction_count,
                "total_volume": int(result.total_volume) if result.total_volume else 0,
                "total_value": float(result.total_value) if result.total_value else 0
            } for result in results]
        except Exception:
            return []

    def _get_resource_distribution(self, transactions) -> Dict[str, Any]:
        """Calculate resource type distribution"""
        distribution = {}

        for transaction in transactions:
            commodity = transaction.commodity
            if commodity not in distribution:
                distribution[commodity] = {
                    "count": 0,
                    "volume": 0,
                    "value": 0
                }

            distribution[commodity]["count"] += 1
            distribution[commodity]["volume"] += transaction.quantity
            distribution[commodity]["value"] += float(transaction.total_value)

        return distribution

    def _calculate_inflation_rates(self) -> Dict[str, float]:
        """Calculate inflation rates for each resource"""
        inflation = {}

        try:
            # Compare current prices to 24h ago
            now = datetime.utcnow()
            day_ago = now - timedelta(days=1)

            for resource_name in COMMODITY_BASE_PRICES:
                # Get current average buy price
                current_avg = (
                    self.db.query(func.avg(MarketPrice.buy_price))
                    .filter(
                        MarketPrice.commodity == resource_name,
                        MarketPrice.updated_at >= now - timedelta(hours=1)
                    )
                    .scalar()
                )

                # Get past average from price history
                past_avg = (
                    self.db.query(func.avg(PriceHistory.buy_price))
                    .filter(
                        PriceHistory.commodity == resource_name,
                        PriceHistory.snapshot_date >= day_ago - timedelta(hours=1),
                        PriceHistory.snapshot_date <= day_ago + timedelta(hours=1)
                    )
                    .scalar()
                )

                if current_avg and past_avg and past_avg > 0:
                    inflation[resource_name] = round(((current_avg - past_avg) / past_avg) * 100, 2)
                else:
                    inflation[resource_name] = 0.0
        except Exception:
            pass

        return inflation

    def _calculate_market_liquidity(self) -> Dict[str, Any]:
        """Calculate market liquidity metrics"""
        try:
            # Get active ports count
            active_ports = (
                self.db.query(func.count(func.distinct(MarketTransaction.station_id)))
                .filter(MarketTransaction.timestamp >= datetime.utcnow() - timedelta(hours=24))
                .scalar()
            ) or 0

            # Get bid-ask spreads
            spreads = {}
            for resource_name in COMMODITY_BASE_PRICES:
                prices = (
                    self.db.query(MarketPrice.buy_price, MarketPrice.sell_price)
                    .filter(MarketPrice.commodity == resource_name)
                    .all()
                )

                if prices:
                    valid_spreads = [
                        (p.sell_price - p.buy_price) / p.sell_price * 100
                        for p in prices if p.sell_price > 0
                    ]
                    if valid_spreads:
                        spreads[resource_name] = round(sum(valid_spreads) / len(valid_spreads), 2)

            return {
                "active_ports": active_ports,
                "average_spreads": spreads,
                "liquidity_score": self._calculate_liquidity_score(active_ports, spreads)
            }
        except Exception:
            return {
                "active_ports": 0,
                "average_spreads": {},
                "liquidity_score": 0
            }

    def _calculate_wealth_distribution(self) -> Dict[str, Any]:
        """Calculate wealth distribution metrics"""
        try:
            # Get player wealth data
            players = self.db.query(Player.credits).filter(Player.is_active == True).all()

            if not players:
                return {"gini_coefficient": 0, "wealth_brackets": {}, "total_players": 0, "median_wealth": 0}

            credits = sorted([p.credits for p in players])
            total_players = len(credits)

            # Calculate Gini coefficient
            cumsum = 0
            total_credits = sum(credits)
            for i, credit in enumerate(credits):
                cumsum += (2 * i - total_players + 1) * credit

            gini = cumsum / (total_players * total_credits) if total_credits > 0 else 0

            # Wealth brackets
            brackets = {
                "poor": len([c for c in credits if c < 10000]),
                "middle": len([c for c in credits if 10000 <= c < 100000]),
                "wealthy": len([c for c in credits if 100000 <= c < 1000000]),
                "ultra_wealthy": len([c for c in credits if c >= 1000000])
            }

            return {
                "gini_coefficient": round(abs(gini), 3),
                "wealth_brackets": brackets,
                "total_players": total_players,
                "median_wealth": credits[total_players // 2] if credits else 0
            }
        except Exception:
            return {"gini_coefficient": 0, "wealth_brackets": {}, "total_players": 0, "median_wealth": 0}

    def _calculate_market_velocity(self) -> float:
        """Calculate how fast money changes hands"""
        try:
            # Total transaction value in last 24h
            daily_volume = (
                self.db.query(func.sum(MarketTransaction.total_value))
                .filter(MarketTransaction.timestamp >= datetime.utcnow() - timedelta(days=1))
                .scalar() or 0
            )

            # Total money supply
            money_supply = self._calculate_money_supply()

            # Velocity = Transaction Volume / Money Supply
            return float(daily_volume / money_supply) if money_supply > 0 else 0
        except Exception:
            return 0.0

    def _calculate_gdp(self) -> float:
        """Calculate gross domestic product (total economic output)"""
        try:
            return float(
                self.db.query(func.sum(MarketTransaction.total_value))
                .filter(MarketTransaction.timestamp >= datetime.utcnow() - timedelta(days=1))
                .scalar() or 0
            )
        except Exception:
            return 0.0

    def _calculate_money_supply(self) -> float:
        """Calculate total money in circulation"""
        try:
            return float(
                self.db.query(func.sum(Player.credits))
                .filter(Player.is_active == True)
                .scalar() or 0
            )
        except Exception:
            return 0.0

    def _get_average_prices(self) -> Dict[str, float]:
        """Get current average prices for all resources"""
        prices = {}

        try:
            for resource_name in COMMODITY_BASE_PRICES:
                avg_price = (
                    self.db.query(func.avg(MarketPrice.buy_price))
                    .filter(MarketPrice.commodity == resource_name)
                    .scalar()
                )
                prices[resource_name] = float(avg_price) if avg_price else 0
        except Exception:
            pass

        return prices

    def _calculate_price_volatility(self) -> Dict[str, float]:
        """Calculate price volatility for each resource"""
        volatility = {}

        try:
            for resource_name in COMMODITY_BASE_PRICES:
                # Get price history for last 24h
                prices = (
                    self.db.query(PriceHistory.buy_price)
                    .filter(
                        PriceHistory.commodity == resource_name,
                        PriceHistory.snapshot_date >= datetime.utcnow() - timedelta(days=1)
                    )
                    .all()
                )

                if len(prices) > 1:
                    price_values = [float(p.buy_price) for p in prices]
                    avg = sum(price_values) / len(price_values)
                    variance = sum((p - avg) ** 2 for p in price_values) / len(price_values)
                    std_dev = variance ** 0.5
                    volatility[resource_name] = round((std_dev / avg * 100) if avg > 0 else 0, 2)
                else:
                    volatility[resource_name] = 0
        except Exception:
            pass

        return volatility

    def _calculate_health_score(self, indicators: Dict[str, Any],
                               velocity: float,
                               wealth_dist: Dict[str, Any]) -> float:
        """Calculate overall economic health score (0-100)"""
        score = 100.0

        # Deduct for high inflation (any resource > 10%)
        high_inflation = sum(1 for rate in indicators.get('price_volatility', {}).values() if rate > 10)
        score -= high_inflation * 5

        # Deduct for wealth inequality
        gini = wealth_dist.get('gini_coefficient', 0)
        if gini > 0.7:
            score -= 20
        elif gini > 0.5:
            score -= 10

        # Deduct for low velocity
        if velocity < 0.1:
            score -= 15
        elif velocity < 0.3:
            score -= 5

        # Bonus for balanced wealth distribution
        brackets = wealth_dist.get('wealth_brackets', {})
        total = sum(brackets.values()) if brackets else 0
        if total > 0:
            middle_percent = brackets.get('middle', 0) / total
            if middle_percent > 0.5:
                score += 10

        return max(0, min(100, score))

    def _calculate_alert_severity(self, price_change: float) -> str:
        """Calculate alert severity based on price change"""
        abs_change = abs(price_change)

        if abs_change >= 50:
            return "critical"
        elif abs_change >= 30:
            return "high"
        elif abs_change >= 20:
            return "medium"
        else:
            return "low"

    def _get_recommended_action(self, price_change: float, resource_type: str) -> str:
        """Get recommended action for price alert"""
        if price_change > 30:
            return f"Consider injecting {resource_type} supply to stabilize prices"
        elif price_change < -30:
            return f"Consider buying {resource_type} to support price floor"
        elif abs(price_change) > 20:
            return "Monitor closely, prepare for intervention if trend continues"
        else:
            return "Continue monitoring, no immediate action required"

    def _detect_market_manipulation(self) -> List[Dict[str, Any]]:
        """Detect potential market manipulation patterns"""
        alerts = []
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)

        try:
            # Check for wash trading (same player buying/selling repeatedly)
            wash_trades = (
                self.db.query(
                    MarketTransaction.player_id,
                    MarketTransaction.station_id,
                    MarketTransaction.commodity,
                    func.count(MarketTransaction.id).label('trade_count')
                )
                .filter(MarketTransaction.timestamp >= one_hour_ago)
                .group_by(
                    MarketTransaction.player_id,
                    MarketTransaction.station_id,
                    MarketTransaction.commodity
                )
                .having(func.count(MarketTransaction.id) > 10)
                .all()
            )

            for trade in wash_trades:
                player = self.db.query(Player).filter(Player.id == trade.player_id).first()
                station = self.db.query(Station).filter(Station.id == trade.station_id).first()

                alerts.append({
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "alert_type": "market_manipulation",
                    "severity": "high",
                    "player_id": str(trade.player_id),
                    "player_name": player.nickname if player else "Unknown",
                    "station_id": str(trade.station_id),
                    "port_name": station.name if station else "Unknown",
                    "resource_type": trade.commodity,
                    "trade_count": trade.trade_count,
                    "description": f"Potential wash trading detected: {trade.trade_count} trades in 1 hour",
                    "recommended_action": "Investigate player trading patterns and consider temporary trading restrictions"
                })
        except Exception:
            pass

        return alerts

    def _adjust_prices(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Adjust market prices for specific resources"""
        resource_type = parameters.get('resource_type')
        adjustment_percent = parameters.get('adjustment_percent', 0)
        port_ids = parameters.get('port_ids', [])

        # Update prices
        query = self.db.query(MarketPrice).filter(
            MarketPrice.commodity == resource_type
        )

        if port_ids:
            query = query.filter(MarketPrice.station_id.in_(port_ids))

        affected_count = 0
        for price in query.all():
            multiplier = 1 + (adjustment_percent / 100)
            price.buy_price = int(price.buy_price * multiplier)
            price.sell_price = int(price.sell_price * multiplier)
            affected_count += 1

        return {
            "affected_ports": affected_count,
            "resource_type": resource_type,
            "adjustment_percent": adjustment_percent
        }

    def _inject_liquidity(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Inject real stock into a station's market (WO-ADM-ECON-TRUTH).

        Writes the injected quantities into the SAME dual storage the
        trade paths mutate on every buy/sell — station.commodities[commodity]
        ["quantity"] (the JSONB TradingService reprices from) plus the
        mirrored MarketPrice.quantity row (see trading.py's buy/sell
        handlers, e.g. :501-517) — then re-syncs pricing off the new stock
        via TradingService.update_market_prices so the injection is both
        persisted and immediately reflected in the price the station quotes.
        Previously this only queried the Station and returned a dict with
        NO write at all — a pure echo of the request.

        A resource_type that doesn't resolve to a canonical commodity is
        rejected (mirrors the 2H _reset_market_prices vocabulary guard); a
        negative or non-numeric quantity is rejected; a commodity the
        station doesn't stock is skipped (that station never carries it —
        not an error, matches how calculate_dynamic_price treats a missing
        entry). Quantity is clamped to the commodity's capacity, the same
        physical bound stock-regen advances toward. If nothing in the
        request resolves to an actual write, this raises rather than
        return a hollow 'success'.
        """
        station_id = parameters.get('station_id')
        resources = parameters.get('resources', {})

        if not resources:
            raise ValueError("No resources specified for injection")

        # Station-first lock order (matches trading.py's buy/sell paths):
        # lock before mutating the commodities JSONB.
        station = (
            self.db.query(Station)
            .filter(Station.id == station_id)
            .with_for_update()
            .first()
        )
        if not station:
            raise ValueError("Station not found")

        commodities = station.commodities or {}
        injected: Dict[str, int] = {}
        skipped: Dict[str, str] = {}

        for resource_type, amount in resources.items():
            canonical = canonical_commodity(resource_type)
            if canonical not in COMMODITY_BASE_PRICES:
                skipped[resource_type] = "unknown commodity"
                continue

            try:
                amount = int(amount)
            except (TypeError, ValueError):
                skipped[resource_type] = "non-numeric quantity"
                continue

            if amount < 0:
                skipped[resource_type] = "negative quantity rejected"
                continue

            # commodities is station.commodities itself (only falls back to
            # a throwaway {} when station.commodities is None, in which case
            # no key can match below) — mutating commodity_cfg in place
            # mutates the real JSONB attribute.
            commodity_cfg = commodities.get(canonical)
            if commodity_cfg is None:
                skipped[resource_type] = "station does not stock this commodity"
                continue

            capacity = commodity_cfg.get("capacity", 0) or 0
            current_qty = commodity_cfg.get("quantity", 0)
            new_qty = min(current_qty + amount, capacity) if capacity > 0 else current_qty + amount
            commodity_cfg["quantity"] = new_qty
            injected[canonical] = new_qty - current_qty

        if not injected:
            raise ValueError(f"No resources injected: {skipped}")

        flag_modified(station, "commodities")
        self.db.flush()

        # Re-sync MarketPrice.quantity + reprice off the updated stock — the
        # same sync the trade paths perform after mutating the commodities
        # JSONB, so the injection is visible to the /admin/economy/market-data
        # read and to the next trade's pricing, not just to the station row.
        TradingService(self.db).update_market_prices(station.id)

        return {
            "station_id": str(station_id),
            "station_name": station.name,
            "resources_injected": injected,
            "skipped": skipped,
        }

    def _freeze_trading(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Trading-freeze has zero consumers repo-wide (grep freeze_trading|
        trading_freeze hits only this file and the admin_economy.py
        docstring) and is off-canon: sw2102-docs/OPERATIONS/admin-ui.md's
        real admin capability list names 'market interventions (price cap /
        floor / supply injection)' but never trading-freeze. Previously this
        returned a canned 'trading_freeze_initiated' dict with no state
        stored anywhere a trade path could ever check — a pure phantom.
        Raise an honest 501 rather than half-implement an unenforced flag
        (DECISIONS Pending: should trading-freeze become a real, enforced
        capability?)."""
        raise InterventionError(
            501,
            "freeze_trading is not implemented — no trade path checks a "
            "freeze flag, so this would be an unenforced no-op. Off-canon "
            "capability; see DECISIONS Pending on whether to build it for real."
        )

    def _reset_market_prices(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Reset market prices to their canonical baseline.

        Baseline now comes from commodity_economy.base_price() — the single
        source of truth every other economy consumer already derives from —
        instead of a hand-maintained dict that wrote non-canon values (e.g.
        fuel 100 vs canon base 12) and carried six slugs no MarketPrice
        writer has ever stored a row under. An unknown/non-canon
        resource_type is now rejected rather than silently defaulted to 100
        (WO-ARCH-RES-2H-RUNTIME-VOCAB — DATA bugfix only; this does not touch
        the admin route's auth/gating)."""
        resource_type = parameters.get('resource_type')
        canonical = canonical_commodity(resource_type) if resource_type else None

        if canonical not in COMMODITY_BASE_PRICES:
            return {
                "resource_type": resource_type,
                "error": f"Unknown commodity '{resource_type}' — no canonical base price",
                "affected_ports": 0,
            }

        baseline = commodity_base_price(canonical)

        # Update all prices (filter on the canonical slug — the wire vocab
        # every MarketPrice writer actually stores, e.g. "fuel_ore" resolves
        # to "ore").
        affected = self.db.query(MarketPrice).filter(
            MarketPrice.commodity == canonical
        ).update({
            "buy_price": int(baseline * 0.9),
            "sell_price": int(baseline * 1.1),
        })

        return {
            "resource_type": resource_type,
            "baseline_price": baseline,
            "affected_ports": affected
        }

    def _calculate_liquidity_score(self, active_ports: int, spreads: Dict[str, float]) -> float:
        """Calculate liquidity score (0-100)"""
        # Base score from active ports
        port_score = min(50, active_ports * 2)  # Max 50 points from ports

        # Average spread score
        if spreads:
            avg_spread = sum(spreads.values()) / len(spreads)
            spread_score = max(0, 50 - avg_spread * 5)  # Lower spreads = higher score
        else:
            spread_score = 0

        return round(port_score + spread_score, 1)
