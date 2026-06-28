"""
Market Prediction Engine using Statistical Analysis

This module implements market price prediction for the Sectorwars2102 game
using moving averages, standard deviations, and trend detection.
No ML libraries required -- pure statistical methods.
"""

import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from src.models.market_transaction import MarketTransaction, MarketPrice, PriceHistory
from src.models.station import Station
from src.utils.error_handling import generate_error_id

logger = logging.getLogger(__name__)


@dataclass
class PricePrediction:
    """A single commodity price prediction."""
    commodity: str
    station_id: str
    current_price: float
    predicted_price: float
    price_change_pct: float
    trend: str  # "rising", "falling", "stable"
    confidence: float  # 0.0 to 1.0
    volatility: float
    lower_bound: float
    upper_bound: float
    prediction_horizon_hours: int
    factors: List[str]
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "station_id": self.station_id,
            "current_price": self.current_price,
            "predicted_price": self.predicted_price,
            "price_change_pct": self.price_change_pct,
            "trend": self.trend,
            "confidence": self.confidence,
            "volatility": self.volatility,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "prediction_horizon_hours": self.prediction_horizon_hours,
            "factors": self.factors,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TradeOpportunity:
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "buy_station_id": self.buy_station_id,
            "buy_sector_id": self.buy_sector_id,
            "buy_price": self.buy_price,
            "sell_station_id": self.sell_station_id,
            "sell_sector_id": self.sell_sector_id,
            "sell_price": self.sell_price,
            "profit_per_unit": self.profit_per_unit,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class MarketPredictionEngine:
    """
    Statistical market prediction engine.

    Uses moving averages, standard deviation bands, and linear trend detection
    to predict commodity prices and identify trading opportunities.
    """

    # Core commodities traded in the game
    COMMODITIES = [
        "ore", "organics", "equipment", "fuel",
        "luxury_goods", "gourmet_food", "exotic_technology", "colonists",
    ]

    def __init__(self):
        self.short_window = 5   # Short-term moving average window
        self.long_window = 20   # Long-term moving average window
        self.model_version = "statistical_1.0.0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def predict_prices(
        self,
        db: AsyncSession,
        commodity: str,
        station_id: Optional[str] = None,
        hours_ahead: int = 24,
    ) -> Optional[PricePrediction]:
        """
        Predict future price for a commodity at a station (or globally).

        Uses:
        1. Exponential moving average for trend direction
        2. Standard-deviation bands for confidence intervals
        3. Linear regression slope for price change magnitude
        """
        try:
            prices = await self._get_price_series(db, commodity, station_id)

            if len(prices) < 3:
                return self._insufficient_data_prediction(
                    commodity, station_id or "global", hours_ahead
                )

            current_price = prices[-1]

            # Calculate moving averages
            short_ma = self._exponential_moving_average(prices, self.short_window)
            long_ma = self._exponential_moving_average(prices, self.long_window)

            # Calculate volatility (standard deviation of returns)
            volatility = self._calculate_volatility(prices)

            # Detect trend via linear regression on recent prices
            recent = prices[-min(len(prices), self.long_window):]
            slope, intercept = self._linear_regression(recent)

            # Determine trend direction from MA crossover + slope
            trend = self._determine_trend(short_ma, long_ma, slope)

            # Project price using slope
            steps_ahead = max(1, hours_ahead)
            raw_prediction = current_price + slope * steps_ahead

            # Dampen extreme predictions towards the long-term mean
            long_term_mean = sum(prices) / len(prices)
            mean_reversion_factor = 0.3  # 30 % pull towards mean
            predicted_price = (
                raw_prediction * (1 - mean_reversion_factor)
                + long_term_mean * mean_reversion_factor
            )
            predicted_price = max(1.0, predicted_price)  # floor at 1 credit

            # Confidence decreases with horizon and volatility
            base_confidence = max(0.2, 1.0 - volatility)
            horizon_decay = max(0.3, 1.0 - (hours_ahead / 168))  # decays over a week
            data_quality = min(1.0, len(prices) / 30)  # more data = higher confidence
            confidence = round(base_confidence * horizon_decay * data_quality, 3)

            # Bounds based on volatility
            spread = current_price * volatility * math.sqrt(hours_ahead / 24)
            lower_bound = max(1.0, predicted_price - spread)
            upper_bound = predicted_price + spread

            price_change_pct = (
                ((predicted_price - current_price) / current_price) * 100
                if current_price > 0 else 0.0
            )

            factors = self._identify_factors(
                prices, short_ma, long_ma, volatility, trend
            )

            return PricePrediction(
                commodity=commodity,
                station_id=station_id or "global",
                current_price=round(current_price, 2),
                predicted_price=round(predicted_price, 2),
                price_change_pct=round(price_change_pct, 2),
                trend=trend,
                confidence=confidence,
                volatility=round(volatility, 4),
                lower_bound=round(lower_bound, 2),
                upper_bound=round(upper_bound, 2),
                prediction_horizon_hours=hours_ahead,
                factors=factors,
                timestamp=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error predicting prices for {commodity}: {e}")
            return None

    async def batch_predict(
        self,
        db: AsyncSession,
        station_id: Optional[str] = None,
        hours_ahead: int = 24,
    ) -> Dict[str, PricePrediction]:
        """Generate predictions for all commodities at a station."""
        predictions: Dict[str, PricePrediction] = {}
        for commodity in self.COMMODITIES:
            pred = await self.predict_prices(db, commodity, station_id, hours_ahead)
            if pred:
                predictions[commodity] = pred
        return predictions

    async def find_opportunities(
        self,
        db: AsyncSession,
        min_profit_margin: float = 0.10,
        limit: int = 10,
    ) -> List[TradeOpportunity]:
        """
        Scan all stations for buy-low / sell-high opportunities.

        Compares current prices to their station-local moving averages to find
        commodities priced significantly below or above average, then pairs
        cheap-buy stations with expensive-sell stations.
        """
        try:
            opportunities: List[TradeOpportunity] = []

            # Get all operational stations with their commodities
            query = select(Station).where(Station.is_destroyed == False)  # noqa: E712
            result = await db.execute(query)
            stations = result.scalars().all()

            if not stations:
                return []

            # Build per-commodity price maps: {commodity: [(station, price, buys, sells)]}
            commodity_map: Dict[str, List[Tuple[Station, float, bool, bool]]] = {}
            for station in stations:
                if not station.commodities:
                    continue
                for commodity_name, cdata in station.commodities.items():
                    if commodity_name not in commodity_map:
                        commodity_map[commodity_name] = []
                    price = cdata.get("current_price", cdata.get("base_price", 0))
                    buys = cdata.get("buys", False)
                    sells = cdata.get("sells", False)
                    qty = cdata.get("quantity", 0)
                    if price > 0 and (buys or sells) and qty > 0:
                        commodity_map[commodity_name].append(
                            (station, float(price), buys, sells)
                        )

            # For each commodity, find profitable station pairs
            for commodity_name, entries in commodity_map.items():
                sellers = [(s, p) for s, p, buys, sells in entries if sells]
                buyers = [(s, p) for s, p, buys, sells in entries if buys]

                if not sellers or not buyers:
                    continue

                # Compute average price for confidence scoring
                all_prices = [p for _, p in sellers] + [p for _, p in buyers]
                avg_price = sum(all_prices) / len(all_prices)

                for sell_station, sell_price in sellers:
                    for buy_station, buy_price in buyers:
                        if str(sell_station.id) == str(buy_station.id):
                            continue

                        profit = buy_price - sell_price  # buy FROM seller, sell TO buyer
                        if sell_price <= 0:
                            continue
                        margin = profit / sell_price

                        if margin >= min_profit_margin:
                            # Confidence based on deviation from average
                            price_deviation = abs(sell_price - avg_price) / avg_price
                            confidence = max(
                                0.3,
                                min(1.0, 0.8 - price_deviation + margin),
                            )

                            opportunities.append(
                                TradeOpportunity(
                                    commodity=commodity_name,
                                    buy_station_id=str(sell_station.id),
                                    buy_sector_id=sell_station.sector_id,
                                    buy_price=sell_price,
                                    sell_station_id=str(buy_station.id),
                                    sell_sector_id=buy_station.sector_id,
                                    sell_price=buy_price,
                                    profit_per_unit=profit,
                                    confidence=round(confidence, 3),
                                    reasoning=(
                                        f"Buy {commodity_name} at sector {sell_station.sector_id} "
                                        f"for {sell_price} credits, sell at sector "
                                        f"{buy_station.sector_id} for {buy_price} credits "
                                        f"({margin*100:.1f}% margin)"
                                    ),
                                )
                            )

            # Sort by profit * confidence and return top results
            opportunities.sort(
                key=lambda o: o.profit_per_unit * o.confidence, reverse=True
            )
            return opportunities[:limit]

        except Exception as e:
            logger.error(f"Error finding trade opportunities: {e}")
            return []

    async def get_commodity_analysis(
        self,
        db: AsyncSession,
        commodity: str,
        station_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return a comprehensive analysis of a commodity's market conditions.
        """
        try:
            prices = await self._get_price_series(db, commodity, station_id)

            if len(prices) < 2:
                return {
                    "commodity": commodity,
                    "status": "insufficient_data",
                    "data_points": len(prices),
                }

            current = prices[-1]
            avg = sum(prices) / len(prices)
            high = max(prices)
            low = min(prices)
            volatility = self._calculate_volatility(prices)
            short_ma = self._exponential_moving_average(prices, self.short_window)
            long_ma = self._exponential_moving_average(prices, self.long_window)
            slope, _ = self._linear_regression(
                prices[-min(len(prices), self.long_window):]
            )
            trend = self._determine_trend(short_ma, long_ma, slope)

            # Deviation from mean
            deviation_pct = ((current - avg) / avg * 100) if avg > 0 else 0

            return {
                "commodity": commodity,
                "station_id": station_id or "global",
                "status": "ok",
                "data_points": len(prices),
                "current_price": round(current, 2),
                "average_price": round(avg, 2),
                "high_price": round(high, 2),
                "low_price": round(low, 2),
                "volatility": round(volatility, 4),
                "trend": trend,
                "short_ma": round(short_ma, 2),
                "long_ma": round(long_ma, 2),
                "deviation_from_mean_pct": round(deviation_pct, 2),
                "slope": round(slope, 4),
                "model_version": self.model_version,
            }

        except Exception as e:
            error_id = generate_error_id()
            logger.exception("Error analysing commodity %s [error_id=%s]", commodity, error_id)
            return {"commodity": commodity, "status": "error", "error_id": error_id}

    # ------------------------------------------------------------------
    # Statistical helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _exponential_moving_average(prices: List[float], window: int) -> float:
        """
        Compute the Exponential Moving Average (EMA) over *prices*.
        If fewer data points than *window*, use all available data.
        """
        if not prices:
            return 0.0
        k = 2 / (min(window, len(prices)) + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = price * k + ema * (1 - k)
        return ema

    @staticmethod
    def _calculate_volatility(prices: List[float]) -> float:
        """
        Annualised volatility proxy: standard deviation of log-returns,
        normalised so the result stays between 0 and 1 for practical use.
        """
        if len(prices) < 2:
            return 0.0

        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(math.log(prices[i] / prices[i - 1]))

        if not returns:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)

        # Clamp to [0, 1]
        return min(1.0, std_dev)

    @staticmethod
    def _linear_regression(
        values: List[float],
    ) -> Tuple[float, float]:
        """
        Simple ordinary-least-squares linear regression.
        Returns (slope, intercept).
        """
        n = len(values)
        if n < 2:
            return 0.0, values[0] if values else 0.0

        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n

        numerator = 0.0
        denominator = 0.0
        for i, y in enumerate(values):
            numerator += (i - x_mean) * (y - y_mean)
            denominator += (i - x_mean) ** 2

        if denominator == 0:
            return 0.0, y_mean

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        return slope, intercept

    @staticmethod
    def _determine_trend(short_ma: float, long_ma: float, slope: float) -> str:
        """Classify trend from MA crossover and regression slope."""
        if short_ma > long_ma * 1.02 and slope > 0:
            return "rising"
        elif short_ma < long_ma * 0.98 and slope < 0:
            return "falling"
        return "stable"

    @staticmethod
    def _identify_factors(
        prices: List[float],
        short_ma: float,
        long_ma: float,
        volatility: float,
        trend: str,
    ) -> List[str]:
        """Produce human-readable factor descriptions."""
        factors: List[str] = []
        if trend == "rising":
            factors.append("Short-term moving average above long-term (bullish crossover)")
        elif trend == "falling":
            factors.append("Short-term moving average below long-term (bearish crossover)")
        else:
            factors.append("Moving averages converging (sideways market)")

        if volatility > 0.3:
            factors.append(f"High volatility ({volatility:.1%}) -- wider prediction bands")
        elif volatility < 0.05:
            factors.append(f"Low volatility ({volatility:.1%}) -- stable pricing expected")

        if len(prices) >= 5:
            recent_change = (prices[-1] - prices[-5]) / prices[-5] * 100 if prices[-5] > 0 else 0
            if abs(recent_change) > 10:
                direction = "surge" if recent_change > 0 else "drop"
                factors.append(
                    f"Recent price {direction} of {abs(recent_change):.1f}% over last 5 data points"
                )

        return factors

    # ------------------------------------------------------------------
    # Data retrieval helpers
    # ------------------------------------------------------------------

    async def _get_price_series(
        self,
        db: AsyncSession,
        commodity: str,
        station_id: Optional[str] = None,
    ) -> List[float]:
        """
        Retrieve historical price data for a commodity.

        Prefers PriceHistory snapshots; falls back to MarketPrice current/previous
        and finally to live Station commodity prices.
        """
        prices: List[float] = []

        try:
            # 1. Try PriceHistory (time-series snapshots)
            query = select(PriceHistory.sell_price).where(
                PriceHistory.commodity == commodity
            ).order_by(PriceHistory.snapshot_date.asc())

            if station_id:
                query = query.where(PriceHistory.station_id == station_id)

            # Last 60 data points
            query = query.limit(60)

            result = await db.execute(query)
            rows = result.scalars().all()
            if rows:
                prices = [float(p) for p in rows if p and p > 0]

            if len(prices) >= 3:
                return prices

            # 2. Fall back to MarketPrice current + previous
            mp_query = select(MarketPrice).where(
                MarketPrice.commodity == commodity
            )
            if station_id:
                mp_query = mp_query.where(MarketPrice.station_id == station_id)

            mp_result = await db.execute(mp_query)
            market_prices = mp_result.scalars().all()

            for mp in market_prices:
                if mp.previous_sell_price and mp.previous_sell_price > 0:
                    prices.append(float(mp.previous_sell_price))
                if mp.sell_price and mp.sell_price > 0:
                    prices.append(float(mp.sell_price))

            if len(prices) >= 3:
                return prices

            # 3. Fall back to Station.commodities JSONB
            st_query = select(Station).where(Station.is_destroyed == False)  # noqa: E712
            if station_id:
                st_query = st_query.where(Station.id == station_id)
            st_result = await db.execute(st_query)
            stations = st_result.scalars().all()

            for station in stations:
                cdata = (station.commodities or {}).get(commodity, {})
                base = cdata.get("base_price", 0)
                current = cdata.get("current_price", 0)
                if base > 0:
                    prices.append(float(base))
                if current > 0 and current != base:
                    prices.append(float(current))

        except Exception as e:
            logger.error(f"Error retrieving price series for {commodity}: {e}")

        return prices

    def _insufficient_data_prediction(
        self, commodity: str, station_id: str, hours_ahead: int
    ) -> PricePrediction:
        """Return a low-confidence prediction when data is scarce."""
        return PricePrediction(
            commodity=commodity,
            station_id=station_id,
            current_price=0.0,
            predicted_price=0.0,
            price_change_pct=0.0,
            trend="unknown",
            confidence=0.1,
            volatility=0.0,
            lower_bound=0.0,
            upper_bound=0.0,
            prediction_horizon_hours=hours_ahead,
            factors=["Insufficient historical data for reliable prediction"],
            timestamp=datetime.utcnow(),
        )
