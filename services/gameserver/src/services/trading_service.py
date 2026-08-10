"""
Trading Service - Supply/demand dynamic pricing engine.

Handles price calculation based on station stock levels, market price updates,
commodity price range enforcement per spec, and periodic stock regeneration.
"""

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, UTC
import logging

from src.core.game_time import canonical_hours_since
from src.core.commodity_economy import get_commodity_price_ranges
from src.models.station import Station, StationClass
from src.models.market_transaction import MarketPrice

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Player-facing price modifiers (ADR-0062 E-D3 canonical stack)
# ----------------------------------------------------------------------
# The ratified *unit-price* stack (sw2102-docs FEATURES/economy/trading.md
# §Price-stacking-order, Max-blessed 2026-06-14; routes/trading.py) is
# multiplicative, general -> specific — tax is NOT in this stack:
#
#   unit_price = station_price
#     x faction_reputation_multiplier   (0.85 .. 1.50 — Exalted to Public Enemy)
#     x personal_reputation_multiplier  (0.90 .. 1.20 — Legendary to Villain)
#     x (1 - rank.trading_bonus / 100)  (routes via RankingService)
#     x (1 + region.tariff_rate)        (trade_bonuses JSONB; see helpers below)
#     x (1 + station.price_adjustment_lever)  (same-owner skip per E-F1)
#   …then clamp unit_price to the commodity band (FINAL).
#
# Region / station TAX is a separate treasury levy on the transaction TOTAL
# after that clamp (buy: total + int(total×tax); sell: total − int(total×tax))
# — never a per-unit stack factor. Do not re-fold tax into the unit formula
# (WO-CANON-FIX-MARKET-PRICING-STACK-ORDER-AND-TAX; market-pricing.md has
# drifted the other way and is the docs half of that WO).
#
# This helper covers the two player-relationship layers — faction reputation
# and personal reputation — plus the permanent +10% first-login negotiation
# bonus (Player.settings.trade_bonus, first_login_service / ADR-0026 FL1).
# Rank, tariff, and the station lever are composed in the routes alongside
# compute_region_tariff_rate / compute_station_price_lever below.
# Direction: multipliers express "what the player PAYS" — > 1.0 = worse deal.
# On BUY apply as-is; on SELL the route divides station buy_price by the
# player-pays multiplier (and inverts the rest of the stack).

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


# ----------------------------------------------------------------------
# Region tariff + station lever (ADR-0062 E-D3 tail factors, E-F1/E-F2)
# ----------------------------------------------------------------------
# These complete the canonical unit-price stack (rank, rep x2, tariff, lever
# -- see market-pricing.md's "Player-facing price modifiers"). Region TAX is
# NOT part of this stack -- it's a flat treasury levy applied to the
# transaction TOTAL, after the unit price is clamped to the commodity band
# (routes/trading.py's compute_buy_totals/compute_sell_totals). The rep
# layers + first-login are compute_player_price_multiplier (above); rank
# discount is applied in routes/trading.py alongside these two helpers, which
# add the region COMMERCE tariff and the station MARKETING lever. Both
# express "what the player PAYS" (> 1.0 = worse deal); the routes divide them
# back out on a SELL, exactly like the rep multiplier.
#
# STORAGE (no migration — alembic head is stranded): the tariff lives in the
# Region's existing trade_bonuses JSONB under the "tariff_rate" key; the lever
# lives in the Station's existing price_modifiers JSONB under the
# "price_adjustment_lever" key. Both default to 0.0 (neutral).

# E-F2 sliding tariff cap by in-region station count.
TARIFF_CAP_SPARSE = 0.05    # < 3 stations
TARIFF_CAP_MID = 0.15       # 3-5 stations
TARIFF_CAP_DENSE = 0.25     # >= 6 stations (the default ceiling)

# E-D3: region tax is 0-25%; the tariff shares the same 0-25% default ceiling.
STATION_LEVER_BOUND = 0.10  # E-D3: the lever is +/-10%


def region_tariff_cap_for_station_count(station_count: int) -> float:
    """E-F2 sliding tariff cap: sparse regions cannot extract aggressively."""
    if station_count < 3:
        return TARIFF_CAP_SPARSE
    if station_count <= 5:
        return TARIFF_CAP_MID
    return TARIFF_CAP_DENSE


def set_region_tariff(db: Session, region, tariff_rate: float) -> float:
    """E-F2 tariff-set path: store a region tariff in trade_bonuses JSONB,
    clamped to the sliding cap derived from the region's live station count.

    Returns the clamped rate actually stored. Region owners must invest in
    commerce density before they can dial the tariff up. Negative tariffs are
    floored at 0.0 (a tariff is a surcharge, never a subsidy)."""
    from src.models.station import Station
    station_count = (
        db.query(Station).filter(Station.region_id == region.id).count()
    )
    cap = region_tariff_cap_for_station_count(station_count)
    clamped = max(0.0, min(float(tariff_rate or 0.0), cap))
    bonuses = dict(region.trade_bonuses or {})
    bonuses["tariff_rate"] = clamped
    region.trade_bonuses = bonuses
    flag_modified(region, "trade_bonuses")
    return clamped


def compute_region_tariff_multiplier(db: Session, station) -> Tuple[float, float]:
    """Return (multiplier, effective_tariff_rate) for the station's region.

    The tariff is read from Region.trade_bonuses["tariff_rate"] and clamped on
    READ to the E-F2 sliding cap (per the ADR consequence: tariffs above the
    cap clamp on next read, no migration sweep). Multiplier is (1 + tariff).

    Fully defensive: any lookup failure degrades to a neutral (1.0, 0.0) so a
    tariff hiccup never blocks or mis-prices a trade."""
    try:
        region = getattr(station, "region", None)
        if region is None and getattr(station, "region_id", None) is not None:
            from src.models.region import Region
            region = db.query(Region).filter(Region.id == station.region_id).first()
        if region is None:
            return 1.0, 0.0
        bonuses = region.trade_bonuses or {}
        raw = float(bonuses.get("tariff_rate", 0.0) or 0.0)
        if raw <= 0.0:
            return 1.0, 0.0
        from src.models.station import Station
        station_count = (
            db.query(Station).filter(Station.region_id == region.id).count()
        )
        cap = region_tariff_cap_for_station_count(station_count)
        tariff = max(0.0, min(raw, cap))
        return (1.0 + tariff), tariff
    except Exception:
        logger.warning("region tariff lookup failed; using neutral", exc_info=True)
        return 1.0, 0.0


# ----------------------------------------------------------------------
# Region tax (WO-BUILD-REGION-TAX-RATE-WIRING / WO-BUILD-REGION-TAX-REVENUE-
# SHARE-PAYOUT) — distinct from the region TARIFF above and from
# Station.tax_rate. Region.tax_rate (CHECK 0.05-0.25, DECIMAL(5,4)) is a
# governance-set levy on every taxable trade IN the region, independent of
# whether the traded station itself has an owner (station tax is an owner
# lever that goes dark for unowned stations; region tax does not — it is
# the region GOVERNMENT's levy, not a station owner's). Unlike the station
# tax's 40/30/30 defense/owner/operating split (port_ownership_service.
# realize_port_revenue), region tax has a simpler two-way split: 50% to the
# region OWNER (DECISIONS.md region-tax-revenue-share, ratified 2026-08-07,
# first-cut default subject to a future balance-tuning pass) via the
# Central Nexus Bank / wallet, 50% into Region.treasury_balance. This is
# DISTINCT from regional_governance_service.compute_treasury_adjustment /
# POLICY_TREASURY_KEY, which is a manual, policy-driven treasury spend —
# region tax collection is automatic and per-trade.
REGION_TAX_OWNER_SHARE = 0.5


def compute_region_tax_rate(station) -> float:
    """Effective Region.tax_rate for `station`'s region (0.0 if the station
    sits outside any region). Applies regardless of station ownership —
    region tax is a governance levy, not an owner lever."""
    region = getattr(station, "region", None)
    if region is None:
        return 0.0
    return float(region.tax_rate or 0.0)


def realize_region_tax(db: Session, station, tax_amount: int) -> Dict[str, Any]:
    """Realize a collected Region.tax_rate charge: credit Region.treasury_
    balance with a RegionalTreasuryEntry ledger row (ADR-0059 N-I4,
    CAUSE_TAX_COLLECTION), and pay the ratified REGION_TAX_OWNER_SHARE cut
    to Region.owner_id's Player wallet (online) or Central Nexus Bank
    (offline) via central_bank_service.credit_wallet_or_bank.

    No-op (all-zero result) if tax_amount <= 0 or the station has no region.
    An unclaimed region (owner_id is None) — or any failure resolving/
    paying the owner (no linked Player row, DB hiccup) — routes the FULL
    tax_amount to the treasury instead of erroring; the owner cut is folded
    in, never dropped. No commit; caller owns the transaction (mirrors
    realize_port_revenue)."""
    result: Dict[str, Any] = {
        "region_tax": 0, "owner_share": 0, "treasury_share": 0,
        "owner_payout_dest": None,
    }
    if tax_amount <= 0:
        return result
    region = getattr(station, "region", None)
    if region is None and getattr(station, "region_id", None) is not None:
        from src.models.region import Region
        region = db.query(Region).filter(Region.id == station.region_id).first()
    if region is None:
        return result

    from src.models.region import Region, RegionalTreasuryEntry
    region = (
        db.query(Region)
        .filter(Region.id == region.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if region is None:
        return result

    owner_share = int(tax_amount * REGION_TAX_OWNER_SHARE) if region.owner_id is not None else 0
    treasury_share = tax_amount - owner_share

    owner_payout_dest = None
    if owner_share > 0:
        try:
            from src.models.user import User
            owner_user = db.query(User).filter(User.id == region.owner_id).first()
            owner_player = getattr(owner_user, "player", None) if owner_user else None
            if owner_player is None:
                raise ValueError("region owner has no linked Player row")
            from src.services import central_bank_service
            owner_payout_dest = central_bank_service.credit_wallet_or_bank(
                db, owner_player, owner_share,
                entry_type="region_tax_revenue_share",
                source=f"region:{region.id}",
                notes=f"{int(REGION_TAX_OWNER_SHARE * 100)}% region-tax revenue share (station {station.id})",
            )
        except Exception:
            logger.warning(
                "region tax owner payout failed; folding owner share into treasury",
                exc_info=True,
            )
            treasury_share += owner_share
            owner_share = 0

    before = int(region.treasury_balance or 0)
    after = before + treasury_share
    region.treasury_balance = after
    db.add(RegionalTreasuryEntry(
        region_id=region.id,
        before_balance=before,
        after_balance=after,
        delta=treasury_share,
        cause_type=RegionalTreasuryEntry.CAUSE_TAX_COLLECTION,
        cause_id=station.id,
        reason=f"Region tax collected via station {station.id}",
    ))

    result.update(
        region_tax=tax_amount,
        owner_share=owner_share,
        treasury_share=treasury_share,
        owner_payout_dest=owner_payout_dest,
    )
    return result


def fallback_credit_region_tax(db: Session, station, tax_amount: int) -> int:
    """Defensive fallback when ``realize_region_tax`` raises mid-trade.

    Credits ``Region.treasury_balance`` only — NEVER ``Station.treasury_balance``
    (the station owner's private treasury). Misrouting region tax there was
    WO-FIX-REGION-TAX-FALLBACK-MISROUTES-STATION-TREASURY.

    Returns the amount credited (0 if ``tax_amount <= 0`` or no region can be
    resolved). Prefers dropping the credits over crediting the station owner.
    No commit; caller owns the transaction. Best-effort ledger row.
    """
    if tax_amount <= 0:
        return 0
    region = getattr(station, "region", None)
    if region is None and getattr(station, "region_id", None) is not None:
        from src.models.region import Region
        try:
            region = db.query(Region).filter(Region.id == station.region_id).first()
        except Exception:
            logger.warning(
                "region tax fallback: region lookup failed for station %s; "
                "dropping %s rather than crediting station treasury",
                getattr(station, "id", None),
                tax_amount,
                exc_info=True,
            )
            return 0
    if region is None:
        logger.warning(
            "region tax fallback: no region on station %s; dropping %s "
            "rather than crediting station treasury",
            getattr(station, "id", None),
            tax_amount,
        )
        return 0

    before = int(region.treasury_balance or 0)
    after = before + tax_amount
    region.treasury_balance = after
    try:
        from src.models.region import RegionalTreasuryEntry
        db.add(RegionalTreasuryEntry(
            region_id=region.id,
            before_balance=before,
            after_balance=after,
            delta=tax_amount,
            cause_type=RegionalTreasuryEntry.CAUSE_TAX_COLLECTION,
            cause_id=getattr(station, "id", None),
            reason=(
                f"Region tax fallback (realize_region_tax failed) "
                f"via station {getattr(station, 'id', None)}"
            ),
        ))
    except Exception:
        logger.warning(
            "region tax fallback ledger row failed; balance still credited",
            exc_info=True,
        )
    return tax_amount


def _buyer_is_station_owner_or_team(db: Session, player, station) -> bool:
    """E-F1 same-owner/team test: the lever is skipped when the buyer owns the
    station, or is on the team that owns it. Station ownership is single-owner
    (Station.owner_id is a Player id); team match is buyer.team_id ==
    owner.team_id (both non-null), mirroring the ADR-0055 same-team pattern."""
    owner_id = getattr(station, "owner_id", None)
    if owner_id is None:
        return False
    if owner_id == player.id:
        return True
    buyer_team = getattr(player, "team_id", None)
    if buyer_team is None:
        return False
    try:
        from src.models.player import Player
        owner = db.query(Player.team_id).filter(Player.id == owner_id).first()
        if owner is not None and owner[0] is not None and owner[0] == buyer_team:
            return True
    except Exception:
        logger.warning("E-F1 same-team lever check failed; applying lever", exc_info=True)
    return False


def compute_station_lever_multiplier(db: Session, player, station) -> Tuple[float, float]:
    """Return (multiplier, effective_lever) for the station marketing lever.

    Read from Station.price_modifiers["price_adjustment_lever"], clamped to
    +/-10% (E-D3). E-F1: a same-owner / same-team buyer skips the lever
    entirely (returns 1.0, 0.0) — this closes the region-owner self-trade
    arbitrage cycle. Multiplier is (1 + lever).

    Fully defensive: any failure degrades to neutral (1.0, 0.0)."""
    try:
        if _buyer_is_station_owner_or_team(db, player, station):
            return 1.0, 0.0
        modifiers = getattr(station, "price_modifiers", None) or {}
        raw = float(modifiers.get("price_adjustment_lever", 0.0) or 0.0)
        lever = max(-STATION_LEVER_BOUND, min(raw, STATION_LEVER_BOUND))
        if lever == 0.0:
            return 1.0, 0.0
        return (1.0 + lever), lever
    except Exception:
        logger.warning("station lever lookup failed; using neutral", exc_info=True)
        return 1.0, 0.0


# ----------------------------------------------------------------------
# GameEvent / EventEffect consumer (WO-G17)
# ----------------------------------------------------------------------
# GameEvent + EventEffect had full admin CRUD but ZERO gameplay consumers:
# admins could schedule a "price_modifier" event and it never moved a single
# trade. This reader is the first consumer. It returns the SUMMED additive
# delta of every currently-active matching EventEffect, expressed so it folds
# into the price stack with the SAME (1 + delta) convention as the region
# tariff and station lever above.
#
# Active-in-window definition (matches the model + the admin CRUD that flips
# status to ACTIVE):
#   GameEvent.status == EventStatus.ACTIVE
#   GameEvent.start_time <= now <= GameEvent.end_time
#   EventEffect.effect_type == effect_type
#   EventEffect.is_active is True
#   (EventEffect.expires_at is null OR expires_at > now)
#   target filter (when given): EventEffect.target == target  (case-insensitive)
#
# Modifier convention: EventEffect.modifier is stored as a MULTIPLIER (neutral
# 1.0 per the model's column default + the "price_modifier ... multiplier"
# comment). We convert each matching effect to its additive delta (modifier -
# 1.0) and SUM those deltas, so the return is 0.0 when nothing is active and,
# e.g., a single 1.20 event yields +0.20. The caller folds it as
# price *= (1 + delta) — exactly like (1 + tariff_rate) / (1 + lever) — so a
# fully neutral return leaves the price untouched. Fully defensive: any failure
# (missing tables, bad row, db hiccup) degrades to 0.0 — a price calc must NEVER
# 500 over a cosmetic admin event.
def get_active_event_modifiers(
    db: Session,
    effect_type: str,
    target: Optional[str] = None,
) -> float:
    """Return the summed additive modifier-delta for active in-window events.

    Reads GameEvent rows with status ACTIVE whose [start_time, end_time] window
    contains 'now' (UTC), joins their active, non-expired EventEffect rows of
    the given effect_type (optionally filtered to a target/scope), and sums each
    effect's (modifier - 1.0) into one additive delta. Returns 0.0 (neutral)
    when nothing matches. Never raises into a trade."""
    try:
        from src.models.game_event import GameEvent, EventEffect, EventStatus

        now = datetime.now(UTC)
        q = (
            db.query(EventEffect.modifier, EventEffect.target)
            .join(GameEvent, EventEffect.event_id == GameEvent.id)
            .filter(GameEvent.status == EventStatus.ACTIVE)
            .filter(GameEvent.start_time <= now)
            .filter(GameEvent.end_time >= now)
            .filter(EventEffect.effect_type == effect_type)
            .filter(EventEffect.is_active.is_(True))
            .filter(
                (EventEffect.expires_at.is_(None))
                | (EventEffect.expires_at > now)
            )
        )

        target_norm = target.strip().lower() if isinstance(target, str) else None
        total_delta = 0.0
        for modifier, eff_target in q.all():
            # Target filter (when requested): exact, case-insensitive. A global
            # effect with no target ("" / None) is treated as untargeted and
            # only matches when no target filter is supplied.
            if target_norm is not None:
                row_target = (eff_target or "").strip().lower()
                if row_target != target_norm:
                    continue
            try:
                total_delta += float(modifier) - 1.0
            except (TypeError, ValueError):
                continue
        return total_delta
    except Exception:
        logger.warning(
            "active event-modifier lookup failed; using neutral", exc_info=True
        )
        return 0.0


# Spec-defined price ranges per commodity (from Resources.aispec;
# precious_metals per sw2102-docs ADR-0062 E-D1: 80-180 cr/unit, slotted
# between equipment and exotic_technology).
#
# WO-Y / ADR-0082: these ranges now derive from the SINGLE source of truth in
# src.core.commodity_economy (which also feeds the citadel safe credit values),
# so trading and construction/credit valuation can no longer silently disagree.
# This is a behaviour-preserving alias — get_commodity_price_ranges() reproduces
# the exact prior values (guarded by import-time assertions in that module).
COMMODITY_PRICE_RANGES: Dict[str, Dict[str, int]] = get_commodity_price_ranges()


def clamp_to_commodity_band(commodity_name: str, price: int) -> int:
    """Clamp a final per-unit price to the commodity's hard [min, max] band.

    Canon (trading.md#price-stacking-order, blessed by human 2026-06-14): the
    commodity-specific [min, max] range is the ABSOLUTE floor/ceiling on the
    final per-unit price — it is the LAST step, applied AFTER every multiplicative
    modifier (faction reputation × personal reputation × military rank ×
    Class-8/9/11 premium × region tariff × station lever). No stack of modifiers
    may carry a price outside the band in either trade direction.

    The route applies its modifiers AFTER reading the (already band-clamped)
    persisted MarketPrice, so those modifiers can re-escape the band; this helper
    is the final re-clamp on the route's per-unit price for both buy and sell.
    Commodities absent from COMMODITY_PRICE_RANGES are returned unbounded
    (floored at 1) — matching calculate_dynamic_price, which only clamps known
    commodities.
    """
    price = max(1, int(price))
    price_range = COMMODITY_PRICE_RANGES.get(commodity_name)
    if price_range:
        price = max(price_range["min"], min(price, price_range["max"]))
    return price


# Sell/buy price spread factor — stations sell higher and buy lower
# This creates the profit margin that drives inter-station trade routes
SELL_SPREAD = 1.15   # Station sell price is 15% above dynamic midpoint
BUY_SPREAD = 0.85    # Station buy price is 15% below dynamic midpoint

# TradeDock premium spreads (FEATURES/economy/tradedock-shipyard.md:44-60).
# Canon gives ranges; midpoints keep a single deterministic engine value.
TRADEDOCK_SELL_SPREAD = 1.075  # mid of 1.05–1.10
TRADEDOCK_BUY_SPREAD = 0.925   # mid of 0.90–0.95
# Canon Class-0 platform fee is 2% (tradedock-shipyard.md:48). That levy was
# never a separate live charge before this WO (only station/region tax_rate).
# Flipping it on here would reprice every NPC trade overnight — leave at 0.0
# until a dedicated fee-activation pass; TradeDock still resolves through
# transaction_fee_rate() → 0.0 so the branch is wired.
STANDARD_TRANSACTION_FEE = 0.0
TRADEDOCK_INVENTORY_CAP_MULT = 10
TRADEDOCK_BULK_DISCOUNT_PER_1000 = 0.05
TRADEDOCK_BULK_DISCOUNT_CAP = 0.20


def is_tradedock(station: Station) -> bool:
    """True when Station.tradedock_tier is set (A/B/C/…), else ordinary port."""
    return getattr(station, "tradedock_tier", None) is not None


def spreads_for(station: Station) -> Tuple[float, float]:
    """Return (sell_spread, buy_spread) for this station."""
    if is_tradedock(station):
        return TRADEDOCK_SELL_SPREAD, TRADEDOCK_BUY_SPREAD
    return SELL_SPREAD, BUY_SPREAD


def transaction_fee_rate(station: Station) -> float:
    """Platform transaction fee fraction (0 for TradeDocks, 2% otherwise)."""
    return 0.0 if is_tradedock(station) else STANDARD_TRANSACTION_FEE


def inventory_capacity_for(station: Station, base_capacity: int) -> int:
    """Effective stock ceiling — TradeDocks get 10× the commodity capacity."""
    cap = max(1, int(base_capacity or 1))
    if is_tradedock(station):
        return cap * TRADEDOCK_INVENTORY_CAP_MULT
    return cap


def tradedock_bulk_discount_fraction(quantity: int) -> float:
    """5% per 1,000 units on TradeDock buys, capped at 20% (canon ladder)."""
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return 0.0
    if qty < 1000:
        return 0.0
    return min(
        TRADEDOCK_BULK_DISCOUNT_CAP,
        (qty // 1000) * TRADEDOCK_BULK_DISCOUNT_PER_1000,
    )


# Station-class premium multipliers, applied at transaction-price time so
# they survive every dynamic reprice. Values are the trading.md
# #class-8--class-9-premium-pricing design target (+20% buy / +25% sell,
# both UPWARD: Class 8 pays players more, Class 9 charges players more).
# Live premiums — apply_stock_levels bootstraps current_price to base only
# (the old inverted Class-9 0.8x bootstrap was deleted; see station_class_map).
CLASS_8_BUY_PREMIUM = 1.2    # Black Hole pays players 20% more for what it buys
CLASS_9_SELL_PREMIUM = 1.25  # Nova charges players 25% more for what it sells

# Lazy stock-regen tick length, in CANONICAL hours. trading.md#stock-regen
# specifies the advance formula (quantity = min(capacity, quantity +
# production_rate)) but no tick period — production_rate is interpreted as
# units per canonical HOUR, matching tick_production's long-standing
# "once per game tick / hour" docstring. NO-CANON on the period itself.
REGEN_TICK_HOURS = 1.0

# Per-station wall-clock price-recompute debounce window, in SECONDS
# (ADR-0051 SK30, WO-DBB-EC4). A full price recompute on the hot market-read
# path is rate-limited to at most once per this window per station; a recompute
# attempt inside the window is suppressed and instead flags the station for a
# deferred reprice via flush_pending_recomputes (driven by the npc_scheduler).
# This is a WALL-CLOCK debounce (real seconds), ORTHOGONAL to REGEN_TICK_HOURS
# (which gates the canonical-hours lazy stock-regen tick). It exists to cap the
# repricing churn when many players hammer the same station within a second.
EC4_RECOMPUTE_WINDOW_SECONDS = 1.0


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
        # Supply-ratio uses the commodity's stored capacity (not the TradeDock
        # 10× production ceiling) so a half-full dock prices like a half-full
        # port; the 10× multiplier only raises tick_production's hard ceiling.
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

        # ADR-0062 E-V4: NPC restock demand is a SEPARATE signal from the
        # player demand score — they never blend. npc_restock_demand (written
        # by npc_trading_service, neutral at 1.0) reflects NPC-trader order
        # pressure on this commodity; when NPC traders are hungry for it
        # (> 1.0) it tightens supply and lifts the price, when they are
        # offloading it (< 1.0) it softens the price. Applied as a bounded
        # nudge on the supply midpoint so it nudges prices without dominating
        # the player-driven supply/demand core. The PLAYER demand score is
        # never read here (it drives the UI indicator only), preserving the
        # E-V4 separation — NPC manipulation cannot skew the player signal.
        npc_restock_demand = commodity.get("npc_restock_demand", 1.0)
        try:
            npc_factor = float(npc_restock_demand)
        except (TypeError, ValueError):
            npc_factor = 1.0
        # Map demand 0..2 (neutral 1.0) to a price nudge of +/-15%, clamped.
        npc_nudge = 1.0 + max(-0.15, min(0.15, (npc_factor - 1.0) * 0.15))
        midpoint *= npc_nudge

        # Apply spread based on transaction direction (TradeDock vs Class-0+)
        sell_spread, buy_spread = spreads_for(station)
        if transaction_type == "sell":
            # Station sells TO player — player pays more
            raw_price = midpoint * sell_spread
        elif transaction_type == "buy":
            # Station buys FROM player — player receives less
            raw_price = midpoint * buy_spread
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

        # WO-G17: fold in any ACTIVE price_modifier GameEvent for this commodity.
        # Applied at the SAME stage as the station-class premium — after the
        # supply/demand spread, BEFORE the canon clamp — so an admin-scheduled
        # event shifts trade prices but can never push a price outside the
        # commodity's hard [min, max] band. get_active_event_modifiers returns a
        # summed additive delta (0.0 neutral), folded multiplicatively as
        # (1 + delta) to match the region-tariff / station-lever convention.
        # A single clean fold; never double-applied. Defensive/neutral on any
        # lookup failure (a price calc must not 500 over an event).
        event_delta = get_active_event_modifiers(
            self.db, "price_modifier", target=commodity_name
        )
        if event_delta:
            raw_price *= max(0.0, 1.0 + event_delta)

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

            # Fire any threshold-crossing price alerts for this commodity
            # (canon "Price-alert evaluation cycle", market-pricing.md). The
            # evaluator self-handles cooldown/idempotence and never raises on a
            # delivery failure, but we still wrap defensively: a price-alert
            # hiccup must NEVER break the market price update. Import locally to
            # avoid any import cycle, mirroring the evaluator's own deferred
            # websocket_service import.
            try:
                from src.services.price_alert_service import evaluate_price_alerts

                evaluate_price_alerts(
                    self.db,
                    station_id,
                    commodity_name,
                    commodity_data["current_price"],
                )
            except Exception:
                logger.warning(
                    "Price-alert evaluation failed for station %s commodity %s "
                    "(market update unaffected)",
                    station_id,
                    commodity_name,
                    exc_info=True,
                )

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

    def maybe_recompute_price(self, station_id) -> Dict[str, Any]:
        """Rate-limited price recompute on the hot market-read path
        (ADR-0051 SK30, WO-DBB-EC4).

        At most one full recompute per EC4_RECOMPUTE_WINDOW_SECONDS of
        WALL-CLOCK time per station. The station row is locked first
        (station-first lock order, matching update_market_prices and the trade
        paths); update_market_prices re-locks the same row within this same
        transaction, which is fine — re-entrant on one connection.

        Behavior:
          * If last_price_recomputed_at is set AND less than the window has
            elapsed since it, set pending_price_recomputation=True and return
            {} WITHOUT recomputing (rate-limited). The deferred reprice is
            picked up later by flush_pending_recomputes (driven by the
            npc_scheduler periodic sweep).
          * Otherwise recompute via update_market_prices, stamp
            last_price_recomputed_at=now and clear
            pending_price_recomputation, then return the recompute result.

        The caller owns the commit (the hot read path commits after this).
        """
        # Lock the station row first (same order as update_market_prices /
        # trade paths). populate_existing refreshes the identity-map instance
        # so the window check below reads the latest anchor under the lock.
        station = (
            self.db.query(Station)
            .filter(Station.id == station_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not station:
            logger.error(
                "Station %s not found for rate-limited price recompute", station_id
            )
            return {}

        now = datetime.now(UTC)
        last = station.last_price_recomputed_at
        if last is not None:
            # last is stored timezone-aware (DateTime(timezone=True)); guard a
            # naive value defensively so a stray naive row can't crash the
            # subtraction with a tz-mismatch.
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if (now - last).total_seconds() < EC4_RECOMPUTE_WINDOW_SECONDS:
                # Rate-limited: defer the recompute to the flush sweep.
                station.pending_price_recomputation = True
                self.db.flush()   # autoflush is OFF — flush so the pending flag + window are
                return {}         # visible to the flush sweep + the next call before the request commit

        # Window elapsed (or never recomputed): do the recompute. It re-locks
        # this same row in-txn (re-entrant) and runs the lazy regen tick.
        result = self.update_market_prices(station_id)
        station.last_price_recomputed_at = now
        station.pending_price_recomputation = False
        self.db.flush()   # autoflush OFF: flush the anchor so a subsequent call's locked re-read
                          # (populate_existing) sees it and the 1s window actually rate-limits
        return result

    def flush_pending_recomputes(self) -> int:
        """Reprice every station flagged pending_price_recomputation
        (ADR-0051 SK30, WO-DBB-EC4) — the deferred half of the EC4 rate limit.

        maybe_recompute_price sets pending_price_recomputation=True when a hot
        read tried to recompute inside the wall-clock window; this sweep (wired
        into the npc_scheduler periodic loop) settles those deferred reprices so
        a flagged station does not stay stale indefinitely.

        Per-station defensive try/except so one bad station cannot abort the
        sweep. The caller owns the session lifecycle; update_market_prices
        flushes its own changes. Returns the count of stations recomputed.
        """
        flushed = 0
        pending_ids = (
            self.db.query(Station.id)
            .filter(Station.pending_price_recomputation.is_(True))
            .all()
        )
        now = datetime.now(UTC)
        for (station_id,) in pending_ids:
            # Per-station SAVEPOINT isolation (WO-B1 pattern, sweep_pending_engagements 4dac148): a
            # DB-level error (deadlock on the lock, MarketPrice upsert IntegrityError, flush) aborts
            # the whole transaction — a bare try/except would catch the Python exception but leave the
            # session poisoned, failing every later iteration + the caller's commit (discarding all
            # earlier reprices). begin_nested() rolls back only this row, releasing its lock, so the
            # sweep genuinely continues with earlier rows intact.
            sp = self.db.begin_nested()
            try:
                # Re-read + lock the station so a concurrent recompute can't
                # race us, and re-check the flag under the lock (a hot read may
                # have already cleared it between the candidate query and here).
                station = (
                    self.db.query(Station)
                    .filter(Station.id == station_id)
                    .populate_existing()
                    .with_for_update()
                    .first()
                )
                if station is None or not station.pending_price_recomputation:
                    sp.rollback()
                    continue
                self.update_market_prices(station_id)
                station.last_price_recomputed_at = now
                station.pending_price_recomputation = False
                sp.commit()
                flushed += 1
            except Exception:
                sp.rollback()
                logger.warning(
                    "flush_pending_recomputes: reprice failed for station %s "
                    "(sweep continues)",
                    station_id,
                    exc_info=True,
                )
        if flushed:
            logger.info(
                "flush_pending_recomputes: recomputed %d pending station(s)",
                flushed,
            )
        return flushed

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

    def maybe_fire_price_alert(
        self, market_price, station_name: str = ""
    ) -> bool:
        """Auto-fire a PriceAlert row when a price crosses its configured
        threshold on a trade (ADR-0062 market-data hardening).

        Threshold source: the MarketPrice.alert_threshold column (a fractional
        change, e.g. 0.10 = 10%). Compares the new sell_price against the
        previous_sell_price captured by update_market_prices; if the magnitude
        of the change meets/exceeds the threshold, an active PriceAlert is
        recorded (price_spike on a rise, price_drop on a fall).

        Idempotent within a window: skips if an active alert of the same type
        already exists for this station/commodity. Fully defensive — never
        raises into the trade path; returns True iff an alert was created.
        Caller owns the commit."""
        try:
            threshold = market_price.alert_threshold
            if not threshold or threshold <= 0:
                return False
            prev = market_price.previous_sell_price
            new = market_price.sell_price
            if not prev or prev <= 0 or new is None:
                return False
            change = (new - prev) / prev
            if abs(change) < float(threshold):
                return False

            from src.models.market_transaction import PriceAlert
            alert_type = "price_spike" if change > 0 else "price_drop"

            existing = (
                self.db.query(PriceAlert.id)
                .filter(
                    PriceAlert.station_id == market_price.station_id,
                    PriceAlert.commodity == market_price.commodity,
                    PriceAlert.alert_type == alert_type,
                    PriceAlert.is_active.is_(True),
                )
                .first()
            )
            if existing:
                return False

            pct = abs(change) * 100.0
            severity = (
                "critical" if pct >= 50 else "high" if pct >= 25 else "medium"
            )
            where = f" at {station_name}" if station_name else ""
            alert = PriceAlert(
                station_id=market_price.station_id,
                commodity=market_price.commodity,
                alert_type=alert_type,
                threshold_value=float(threshold),
                current_value=float(new),
                severity=severity,
                message=(
                    f"{market_price.commodity}{where} {alert_type.replace('_', ' ')}: "
                    f"{prev} -> {new} ({change * 100:+.1f}%)"
                ),
                is_active=True,
            )
            self.db.add(alert)
            return True
        except Exception:
            logger.warning("price-alert auto-fire failed (non-fatal)", exc_info=True)
            return False

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
            capacity = inventory_capacity_for(
                station, commodity_data.get("capacity", 0)
            )

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
        from src.services.station_service import is_station_functional

        # Check if player is docked
        if not player.is_docked:
            return False, "You must be docked at a port to trade"

        # Check if player is in the same sector as the station
        if player.current_sector_id != station.sector_id:
            return False, "You must be in the same sector as the port"

        # Destroyed / 24h rebuild window — trading.md recovery clauses
        if not is_station_functional(station):
            return False, "This station is destroyed and rebuilding — trading is suspended"

        return True, "OK"
