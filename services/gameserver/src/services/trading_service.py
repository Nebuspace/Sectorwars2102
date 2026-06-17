"""
Trading Service - Supply/demand dynamic pricing engine.

Handles price calculation based on station stock levels, market price updates,
commodity price range enforcement per spec, and periodic stock regeneration.
"""

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, UTC
import logging

from src.core.game_time import canonical_hours_since
from src.models.station import Station, StationClass
from src.models.market_transaction import MarketPrice

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Player-facing price modifiers (ADR-0062 E-D3 canonical stack)
# ----------------------------------------------------------------------
# The ratified trade-price stack (sw2102-docs ADR-0062 E-D3 /
# SYSTEMS/market-pricing.md) is multiplicative, general -> specific:
#
#   final_price = station_price
#     x faction_reputation_multiplier   (0.85 .. 1.50 — Exalted to Public Enemy)
#     x personal_reputation_multiplier  (0.90 .. 1.20 — Legendary to Villain)
#     x (1 - rank.trading_bonus / 100)  (handled in the routes via RankingService)
#     x (1 + region.tax_rate)           (the routes apply the station tax_rate)
#     x (1 + region.tariff_rate)        (NO-CANON wiring point — see note below)
#     x (1 + station.price_adjustment_lever)  (same-owner skip per E-F1)
#
# This helper covers the two player-relationship layers that were NOT yet
# wired into the trade path — faction reputation and personal reputation —
# plus the permanent +10% first-login negotiation bonus
# (Player.settings.trade_bonus, set by first_login_service per ADR-0026 FL1:
# "Applied at every port transaction for the lifetime of the character").
#
# Rank discount, station tax, tariff, and the station price lever remain the
# routes' responsibility (rank + tax already live there). Direction: all of
# these multipliers express "what the player PAYS" — > 1.0 means a worse
# deal. On a BUY the player pays final_price (multiplier applied as-is); on a
# SELL the relationship flips (a favoured trader earns MORE), so the route
# divides the station's buy_price by the player-pays multiplier.

# Faction-reputation TRADE_MODIFIERS mirror faction_service.TRADE_MODIFIERS
# (kept inline so this sync path needs no async FactionService bridge). Each
# entry is (min_reputation_value, player_pays_multiplier).
_FACTION_TRADE_MODIFIERS = [
    (700, 0.85),   # EXALTED   — 15% better
    (500, 0.90),   # REVERED
    (300, 0.95),   # HONORED
    (100, 0.97),   # FRIENDLY
    (-99, 1.00),   # NEUTRAL
    (-299, 1.05),  # UNFRIENDLY
    (-499, 1.15),  # HOSTILE
    (-699, 1.30),  # HATED
]
_FACTION_TRADE_MODIFIER_PUBLIC_ENEMY = 1.50  # <= -700

# Personal-reputation tier multipliers. ADR-0062 E-D3 fixes the endpoints
# (0.90 Legendary .. 1.20 Villain) but not the per-tier steps; the 8 tiers in
# personal_reputation_service.REPUTATION_TIERS are mapped linearly across that
# band. This is the documented assumption (NO-CANON on the intermediate
# steps); the endpoints match canon exactly.
_PERSONAL_REP_TIER_MULTIPLIERS = {
    "Legendary": 0.90,
    "Heroic": 0.95,
    "Lawful": 0.97,
    "Neutral": 1.00,
    "Suspicious": 1.05,
    "Outlaw": 1.10,
    "Criminal": 1.15,
    "Villain": 1.20,
}


def compute_player_price_multiplier(db: Session, player, station) -> float:
    """Return the canonical player-pays price multiplier for a trade.

    Composes (multiplicatively) the faction-reputation modifier, the
    personal-reputation modifier, and the permanent first-login trade bonus.
    The result is "what the player pays" relative to the station price: a
    value < 1.0 is a discount (good standing), > 1.0 a surcharge.

    Buy path: effective_buy = station_sell_price * multiplier.
    Sell path: effective_sell = station_buy_price / multiplier (the
    relationship flips — a favoured trader is paid MORE).

    Fully defensive: any lookup failure degrades to a 1.0 (neutral) factor so
    a reputation hiccup never blocks or mis-prices a trade beyond neutral.
    """
    multiplier = 1.0

    # --- Faction reputation (station's controlling faction) ---------------
    try:
        faction_name = getattr(station, "faction_affiliation", None)
        if faction_name:
            from src.models.faction import Faction
            from src.models.reputation import Reputation
            faction = (
                db.query(Faction).filter(Faction.name == faction_name).first()
            )
            if faction is not None:
                rep = (
                    db.query(Reputation)
                    .filter(
                        Reputation.player_id == player.id,
                        Reputation.faction_id == faction.id,
                    )
                    .first()
                )
                if rep is not None:
                    value = rep.current_value
                    faction_mult = _FACTION_TRADE_MODIFIER_PUBLIC_ENEMY
                    for threshold, mod in _FACTION_TRADE_MODIFIERS:
                        if value >= threshold:
                            faction_mult = mod
                            break
                    multiplier *= faction_mult
    except Exception:
        logger.warning("faction-rep price modifier failed; using neutral", exc_info=True)

    # --- Personal reputation (legality posture) ---------------------------
    try:
        tier = getattr(player, "reputation_tier", None) or "Neutral"
        personal_mult = _PERSONAL_REP_TIER_MULTIPLIERS.get(tier, 1.0)
        multiplier *= personal_mult
    except Exception:
        logger.warning("personal-rep price modifier failed; using neutral", exc_info=True)

    # --- First-login negotiation bonus (permanent +10% trade bonus) -------
    # Player.settings.trade_bonus = 0.1 when first-login negotiation was
    # strong (ADR-0026 FL1). It is a player-favouring bonus, so it lowers the
    # player-pays multiplier (and is divided back out on sells, raising the
    # payout — symmetric with the rep layers).
    try:
        settings = getattr(player, "settings", None) or {}
        trade_bonus = float(settings.get("trade_bonus", 0.0) or 0.0)
        if trade_bonus > 0.0:
            multiplier *= (1.0 - trade_bonus)
    except Exception:
        logger.warning("first-login trade bonus lookup failed; ignoring", exc_info=True)

    return multiplier

# Spec-defined price ranges per commodity (from Resources.aispec;
# precious_metals per sw2102-docs ADR-0062 E-D1: 80-180 cr/unit, slotted
# between equipment and exotic_technology)
COMMODITY_PRICE_RANGES: Dict[str, Dict[str, int]] = {
    "ore":               {"min": 15,  "max": 45},
    "organics":          {"min": 8,   "max": 25},
    "gourmet_food":      {"min": 30,  "max": 70},
    "fuel":              {"min": 20,  "max": 60},
    "equipment":         {"min": 50,  "max": 120},
    "precious_metals":   {"min": 80,  "max": 180},
    "exotic_technology":  {"min": 150, "max": 300},
    "luxury_goods":      {"min": 75,  "max": 200},
    "colonists":         {"min": 30,  "max": 80},
}

# Sell/buy price spread factor — stations sell higher and buy lower
# This creates the profit margin that drives inter-station trade routes
SELL_SPREAD = 1.15   # Station sell price is 15% above dynamic midpoint
BUY_SPREAD = 0.85    # Station buy price is 15% below dynamic midpoint

# Station-class premium multipliers, applied at transaction-price time so
# they survive every dynamic reprice. Values are the trading.md
# #class-8--class-9-premium-pricing design target (+20% buy / +25% sell,
# both UPWARD: Class 8 pays players more, Class 9 charges players more).
# The bootstrap-only current_price multipliers in
# core/station_class_map.apply_stock_levels (1.3x / 0.8x — Class 9
# DIRECTION inverted vs canon) were never live behavior — they were
# overwritten on the first reprice — so code-wins does not attach to them.
# Conflict recorded in the run-14 report.
CLASS_8_BUY_PREMIUM = 1.2    # Black Hole pays players 20% more for what it buys
CLASS_9_SELL_PREMIUM = 1.25  # Nova charges players 25% more for what it sells

# Lazy stock-regen tick length, in CANONICAL hours. trading.md#stock-regen
# specifies the advance formula (quantity = min(capacity, quantity +
# production_rate)) but no tick period — production_rate is interpreted as
# units per canonical HOUR, matching tick_production's long-standing
# "once per game tick / hour" docstring. NO-CANON on the period itself.
REGEN_TICK_HOURS = 1.0


class TradingService:
    """Service for handling all trading-related operations including
    dynamic supply/demand pricing, market updates, and stock regeneration."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Price Calculation
    # ------------------------------------------------------------------

    def calculate_dynamic_price(
        self,
        station: Station,
        commodity_name: str,
        transaction_type: str,
    ) -> int:
        """Calculate price based on supply/demand at a station.

        When a station has HIGH stock of a commodity it sells, the price DROPS
        (surplus). When stock is LOW, the price RISES (scarcity).

        For buying commodities the inverse applies — low stock means the
        station is desperate to buy (high buy price offered to players),
        high stock means the station is glutted (low buy price).

        Formula:
            supply_ratio = current_quantity / capacity  (0.0 – 1.0)
            midpoint     = base_price * (1.5 - supply_ratio)

        Then we apply a spread:
            sell price (station charges player) = midpoint * SELL_SPREAD
            buy price  (station pays player)    = midpoint * BUY_SPREAD

        Result is clamped to the spec-defined min/max for the commodity.
        """
        commodities = station.commodities or {}
        commodity = commodities.get(commodity_name)
        if commodity is None:
            logger.warning(
                "Commodity %s not found on station %s", commodity_name, station.id
            )
            return 0

        quantity = commodity.get("quantity", 0)
        capacity = commodity.get("capacity", 1)
        base_price = commodity.get("base_price", 0)

        # Avoid division by zero
        if capacity <= 0:
            capacity = 1

        supply_ratio = min(max(quantity / capacity, 0.0), 1.0)

        # Core dynamic: low supply → high price, high supply → low price
        # At supply_ratio=0 → multiplier=1.5 (max scarcity premium)
        # At supply_ratio=1 → multiplier=0.5 (max surplus discount)
        midpoint = base_price * (1.5 - supply_ratio)

        # Apply spread based on transaction direction
        if transaction_type == "sell":
            # Station sells TO player — player pays more
            raw_price = midpoint * SELL_SPREAD
        elif transaction_type == "buy":
            # Station buys FROM player — player receives less
            raw_price = midpoint * BUY_SPREAD
        else:
            raw_price = midpoint

        # Station-class premium (Class 8 "Black Hole" / Class 9 "Nova"):
        # applied here, after the supply/demand spread but BEFORE the canon
        # clamp. Clamp-after-premium ordering is NO-CANON (trading.md is
        # silent on it) — chosen so spec price ranges remain hard bounds;
        # DECISIONS.md Pending entry filed. Premium gates require the trade
        # flag to be EXCLUSIVE (buys and not sells / sells and not buys):
        # the canonical class patterns are one-directional, and gating on a
        # single flag would let a stray both-flag commodity (the model's
        # default dict ships equipment with both) become a same-station
        # buy/sell pump once rank trading bonuses exceed the spread.
        station_class = station.station_class
        if (
            transaction_type == "buy"
            and station_class == StationClass.CLASS_8
            and commodity.get("buys")
            and not commodity.get("sells")
        ):
            raw_price *= CLASS_8_BUY_PREMIUM
        elif (
            transaction_type == "sell"
            and station_class == StationClass.CLASS_9
            and commodity.get("sells")
            and not commodity.get("buys")
        ):
            raw_price *= CLASS_9_SELL_PREMIUM
        elif station_class == StationClass.CLASS_11:
            # Class 11 "Premium Tech Specialist" charges +25% in BOTH directions
            # on its two premium commodities (exotic_technology, luxury_goods).
            # Its commodities carry BOTH trade flags, so use a NON-exclusive gate
            # (the 8/9 exclusive gate would suppress it). Single source of truth:
            # station_class_map.get_class_premium.
            from src.core.station_class_map import get_class_premium
            if transaction_type == "buy" and commodity.get("buys"):
                raw_price *= get_class_premium(StationClass.CLASS_11, "buy")
            elif transaction_type == "sell" and commodity.get("sells"):
                raw_price *= get_class_premium(StationClass.CLASS_11, "sell")

        # Clamp to spec ranges
        price_range = COMMODITY_PRICE_RANGES.get(commodity_name)
        if price_range:
            raw_price = max(price_range["min"], min(raw_price, price_range["max"]))

        return max(1, int(round(raw_price)))

    # ------------------------------------------------------------------
    # Market Price Updates
    # ------------------------------------------------------------------

    def update_market_prices(self, station_id) -> Dict[str, Any]:
        """Recalculate all commodity prices for a station based on current
        stock levels and persist them to the MarketPrice table.

        Runs the lazy stock-regen tick first (see tick_production), so
        repriced quantities already include any production accrued since
        the station's last market update.

        Returns a dict of commodity → {buy_price, sell_price, quantity} for
        every commodity that was updated.
        """
        # Lock the station row (station-first lock order, matching the trade
        # paths): the lazy regen below is read-modify-write on the
        # commodities JSONB, and two concurrent market reads passing the
        # regen gate together would otherwise double-apply production.
        # populate_existing() refreshes the identity-map instance so the
        # gate re-check under the lock sees the latest anchor.
        station = (
            self.db.query(Station)
            .filter(Station.id == station_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not station:
            logger.error("Station %s not found for market price update", station_id)
            return {}

        # LAZY STOCK REGEN (advance-on-read, same shape as the terraforming/
        # citadel lazy ticks): regenerate stock for the canonical hours
        # elapsed since last_market_update — the regen anchor — then reprice
        # from the regenerated quantities. Sub-tick remainders are lost to
        # int truncation (< 1 unit per tick). The credited interval is capped
        # at 24 canonical hours: last_market_update is NOT NULL with a
        # row-insert default, so the first tick after this feature deployed
        # would otherwise retroactively credit all time since galaxy import
        # and snap every market to capacity in one universe-wide refill.
        if station.last_market_update is not None:
            elapsed_hours = min(canonical_hours_since(station.last_market_update), 24.0)
            if elapsed_hours >= REGEN_TICK_HOURS:
                self.tick_production(station, hours=elapsed_hours)

        commodities = station.commodities or {}
        updated: Dict[str, Any] = {}

        for commodity_name, commodity_data in commodities.items():
            sell_price = self.calculate_dynamic_price(station, commodity_name, "sell")
            buy_price = self.calculate_dynamic_price(station, commodity_name, "buy")

            # Ensure sell price >= buy price (station always profits on spread)
            if buy_price >= sell_price:
                buy_price = max(1, sell_price - 1)

            # Update station's JSONB current_price (midpoint for display)
            commodity_data["current_price"] = (sell_price + buy_price) // 2

            # Upsert MarketPrice row
            market_price = (
                self.db.query(MarketPrice)
                .filter(
                    MarketPrice.station_id == station_id,
                    MarketPrice.commodity == commodity_name,
                )
                .first()
            )

            quantity = commodity_data.get("quantity", 0)

            if market_price:
                # Preserve previous prices for trend tracking
                market_price.previous_buy_price = market_price.buy_price
                market_price.previous_sell_price = market_price.sell_price

                # Calculate trend (positive = prices rising)
                old_mid = (
                    (market_price.previous_buy_price or buy_price)
                    + (market_price.previous_sell_price or sell_price)
                ) / 2
                new_mid = (buy_price + sell_price) / 2
                if old_mid > 0:
                    market_price.price_trend = (new_mid - old_mid) / old_mid
                else:
                    market_price.price_trend = 0.0

                market_price.buy_price = buy_price
                market_price.sell_price = sell_price
                market_price.quantity = quantity

                # Update supply/demand levels for analytics
                capacity = commodity_data.get("capacity", 1) or 1
                market_price.supply_level = quantity / capacity
                market_price.demand_level = 1.0 - (quantity / capacity)
            else:
                # Create new MarketPrice entry
                capacity = commodity_data.get("capacity", 1) or 1
                market_price = MarketPrice(
                    station_id=station_id,
                    commodity=commodity_name,
                    buy_price=buy_price,
                    sell_price=sell_price,
                    quantity=quantity,
                    supply_level=quantity / capacity,
                    demand_level=1.0 - (quantity / capacity),
                    price_trend=0.0,
                    volatility=commodity_data.get("price_variance", 0) / 100.0,
                )
                self.db.add(market_price)

            updated[commodity_name] = {
                "buy_price": buy_price,
                "sell_price": sell_price,
                "quantity": quantity,
            }

        # Mark station JSONB as modified so SQLAlchemy persists the change
        flag_modified(station, "commodities")
        station.last_market_update = datetime.now(UTC)

        self.db.flush()

        logger.info(
            "Updated market prices for station %s — %d commodities refreshed",
            station_id,
            len(updated),
        )
        return updated

    def lazy_market_tick(self, station: Station) -> bool:
        """Advance-on-read market tick (terraforming/citadel lazy pattern).

        If at least REGEN_TICK_HOURS canonical hours have elapsed since the
        station's last market update, regenerate stock and reprice via
        update_market_prices. The gate keeps frequent market reads from
        repricing on every request (and from zeroing out sub-unit production
        through repeated int() truncation). update_market_prices re-checks
        the anchor under the station row lock, so two requests racing past
        this unlocked gate cannot double-apply production.

        Returns True if a tick ran (caller should commit), False otherwise.
        """
        anchor = station.last_market_update
        if anchor is not None and canonical_hours_since(anchor) < REGEN_TICK_HOURS:
            return False
        self.update_market_prices(station.id)
        return True

    # ------------------------------------------------------------------
    # Spec Price Ranges
    # ------------------------------------------------------------------

    @staticmethod
    def get_commodity_price_ranges() -> Dict[str, Dict[str, int]]:
        """Return the spec-defined min/max price ranges per commodity.
        Sourced from Resources.aispec."""
        return dict(COMMODITY_PRICE_RANGES)

    # ------------------------------------------------------------------
    # Stock Regeneration
    # ------------------------------------------------------------------

    def tick_production(self, station: Station, hours: float = 1.0) -> Dict[str, int]:
        """Regenerate stock based on each commodity's production_rate.

        Regen formula (trading.md#stock-regen: "each commodity advances
        quantity = min(capacity, quantity + production_rate)"; the doc gives
        no tick period, so production_rate is units per CANONICAL hour):

            quantity = min(capacity, quantity + int(production_rate * hours))

        Called lazily from update_market_prices with the canonical hours
        elapsed since the station's last market update (advance-on-read —
        there is no scheduler). Returns a dict of commodity_name →
        units_produced for commodities that actually gained stock.
        """
        commodities = station.commodities or {}
        produced: Dict[str, int] = {}

        for commodity_name, commodity_data in commodities.items():
            production_rate = commodity_data.get("production_rate", 0)
            if production_rate <= 0:
                continue

            quantity = commodity_data.get("quantity", 0)
            capacity = commodity_data.get("capacity", 0)

            if quantity >= capacity:
                # Already at per-commodity capacity — no production
                continue

            units = int(production_rate * hours)
            if units <= 0:
                continue

            # Produce up to capacity
            new_quantity = min(quantity + units, capacity)
            units_added = new_quantity - quantity
            commodity_data["quantity"] = new_quantity
            produced[commodity_name] = units_added

        if produced:
            flag_modified(station, "commodities")
            logger.debug(
                "Production tick for station %s: %s",
                station.id,
                produced,
            )

        return produced

    # ------------------------------------------------------------------
    # Trade Eligibility
    # ------------------------------------------------------------------

    @staticmethod
    def can_player_trade(player, station) -> Tuple[bool, str]:
        """Check if a player can trade at a specific station.

        Args:
            player: The Player model instance.
            station: The Station model instance.

        Returns:
            Tuple of (can_trade: bool, reason: str).
        """
        # Check if player is docked
        if not player.is_docked:
            return False, "You must be docked at a port to trade"

        # Check if player is in the same sector as the station
        if player.current_sector_id != station.sector_id:
            return False, "You must be in the same sector as the port"

        return True, "OK"
