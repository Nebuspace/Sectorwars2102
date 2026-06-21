from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import List, Dict, Any
from datetime import datetime, UTC
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_user, get_current_player
from src.models.user import User
from src.models.player import Player
from src.models.station import Station
from src.models.sector import Sector
from src.models.ship import Ship, ShipStatus
from src.models.docking import DockingQueueEntry, DockingSlipOccupancy
from src.models.market_transaction import MarketTransaction, MarketPrice, TransactionType
from src.services.trading_service import (
    TradingService,
    clamp_to_commodity_band,
    compute_player_price_multiplier,
    compute_region_tariff_multiplier,
    compute_station_lever_multiplier,
)
from src.services.ranking_service import RankingService
from src.services.medal_service import MedalService
from src.services import docking_service
from src.services.turn_service import spend_turns, regenerate_turns

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading", tags=["trading"])


class TradeRequest(BaseModel):
    station_id: str
    resource_type: str
    quantity: int = Field(..., gt=0, le=100000, description="Must be between 1 and 100,000")


class StationDockRequest(BaseModel):
    station_id: str


class SlipBumpRequest(BaseModel):
    occupant_player_id: str


class LongTermMooringRequest(BaseModel):
    station_id: str
    # Canon (FEATURES/economy/docking-slips): long-term mooring is 1–30 days.
    # The service re-validates against LONG_TERM_MOORING_MAX_DAYS; this bound is
    # the API-surface guard so a bad request is rejected before any DB work.
    days: int = Field(..., ge=1, le=30, description="Mooring duration in days (1–30)")


class MarketInfoResponse(BaseModel):
    resources: Dict[str, Dict[str, Any]]
    port: Dict[str, Any]


def _get_station_or_404(db: Session, station_id: str) -> Station:
    """Fetch a station by id, turning malformed UUIDs into a 404 instead of
    a DataError that surfaces as a generic 'Database error occurred' 500."""
    import uuid as _uuid
    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Station not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


def _ensure_market_prices(db: Session, station: Station) -> None:
    """Bridge the station's commodities JSONB into MarketPrice rows, then
    run the lazy stock-regen tick.

    Galaxy imports (BANG) stock stations only via the commodities JSONB;
    the market/buy/sell endpoints read MarketPrice rows, which otherwise
    never exist — every market in the universe reads empty. Populate them
    lazily on first access.

    LAZY STOCK REGEN (advance-on-read, terraforming/citadel pattern): there
    is no scheduler calling TradingService.tick_production, so without this
    tick station stock only ever drains. Every market read/trade path runs
    through here; when >= 1 canonical hour has elapsed since the station's
    last market update, stock regenerates from production_rate and prices
    recompute from the new supply levels.

    May COMMIT — callers must invoke this before taking any row locks
    (update_market_prices itself locks the station row, station-first).
    """
    if not station.commodities:
        return
    has_rows = db.query(MarketPrice.id).filter(
        MarketPrice.station_id == station.id
    ).first()
    if not has_rows:
        TradingService(db).update_market_prices(station.id)
        db.commit()
    elif TradingService(db).lazy_market_tick(station):
        db.commit()


def _reprice_after_trade(
    db: Session, station: Station, market_price: MarketPrice, commodity: str
) -> bool:
    """Recompute a single commodity's MarketPrice from the post-trade supply,
    capturing previous prices for trend/alert tracking, then auto-fire a
    PriceAlert if the price crossed its configured threshold (ADR-0062
    market-data hardening).

    Fully defensive: a pricing/alert hiccup must never break a trade, so any
    error here is swallowed (the trade's core mutations already happened).
    Returns True iff a PriceAlert row was created."""
    try:
        ts = TradingService(db)
        new_sell = ts.calculate_dynamic_price(station, commodity, "sell")
        new_buy = ts.calculate_dynamic_price(station, commodity, "buy")
        if new_buy >= new_sell:
            new_buy = max(1, new_sell - 1)
        # Preserve prior prices as the alert/trend baseline before overwriting.
        market_price.previous_buy_price = market_price.buy_price
        market_price.previous_sell_price = market_price.sell_price
        market_price.buy_price = new_buy
        market_price.sell_price = new_sell
        return ts.maybe_fire_price_alert(market_price, station_name=station.name or "")
    except Exception:
        logger.warning("post-trade reprice/alert failed (non-fatal)", exc_info=True)
        return False


def _medal_trading_discount_rate(db: Session, player: Player) -> float:
    """WO-CG — the summed, capped medal ``trading_discount`` bonus as a RATE
    (i.e. percent / 100.0) to add into ``rank_rate``.

    Defensive: resolved by import-on-call and degrading to 0.0 on any failure so
    the price computation is never broken by a medal lookup. The result is
    already clamped to the blessed +2% cap by ``get_active_medal_bonuses``."""
    try:
        if player is None or getattr(player, "id", None) is None:
            return 0.0
        from src.services.medal_service import get_active_medal_bonuses
        bonuses = get_active_medal_bonuses(db, player.id) or {}
        return float(bonuses.get("trading_discount", 0.0) or 0.0) / 100.0
    except Exception:
        logger.warning("medal trading-discount read failed; using neutral", exc_info=True)
        return 0.0


def compute_effective_unit_price(
    db: Session,
    player: Player,
    station: Station,
    commodity: str,
    side: str,
    base_price: int,
) -> int:
    """Compute the POSTED per-unit price this player would pay/receive un-haggled.

    This is the single source of truth for the trading.md price stack — the exact
    per-unit price the buy/sell route charges this player BEFORE any haggle:

        BUY  : base_sell_price × (1 − rank_discount) × player_mult × tariff × lever
        SELL : base_buy_price  × (1 + rank_bonus)    ÷ player_mult ÷ tariff ÷ lever

    then clamped LAST to the commodity hard [min, max] band. ``base_price`` is the
    station's prevailing per-unit price for the side (BUY → station sell_price,
    SELL → station buy_price) — i.e. ``calculate_dynamic_price`` for that side.

    ADR-0079 point 6: the numerical haggle engine negotiates off THIS posted
    price (haggling.md:13/:70 — the haggle outcome MULTIPLIES the posted price),
    so haggle ``fair_price`` == this value. The engine's band modifiers (rank /
    faction / personal / difficulty) adjust the ACCEPTANCE BAND only and are NOT
    re-baked into the price here — that is the genuine point-6 rule. Reusing this
    helper guarantees haggle == route on the price, preserving the player's
    rank/rep discount through a haggle.

    Defensive: each modifier helper already degrades to neutral on lookup
    failure, so this never raises."""
    side = side.lower()
    bonuses = RankingService.get_rank_bonuses(player.military_rank)
    # WO-CG: extend the rank_rate term with the summed, capped medal
    # trading_discount bonus (≤ −2% buy / +2% sell from all medals combined),
    # still inside the ADR-0062 price chain — it joins as part of rank_rate, it
    # does not multiply outside it. Positive magnitude = buy discount / sell
    # uplift, the same direction as rank_rate. Defensive: a medal-read failure
    # degrades to the neutral rank-only rate.
    rank_rate = bonuses["trading_discount_percent"] / 100.0
    rank_rate += _medal_trading_discount_rate(db, player)
    player_mult = compute_player_price_multiplier(db, player, station)
    tariff_mult, _ = compute_region_tariff_multiplier(db, station)
    lever_mult, _ = compute_station_lever_multiplier(db, player, station)

    if side == "buy":
        # Player BUYS from the station → pays the station sell price; a discount
        # (rank / favoured rep) LOWERS what the player pays.
        price = base_price * (1 - rank_rate)
        price *= player_mult * tariff_mult * lever_mult
    else:  # sell — player is PAID the station buy price; a favoured player is
        # paid MORE (the relationship flips → divide).
        price = base_price * (1 + rank_rate)
        if player_mult > 0:
            price /= player_mult
        if tariff_mult > 0:
            price /= tariff_mult
        if lever_mult > 0:
            price /= lever_mult

    return clamp_to_commodity_band(commodity, int(price))


async def _publish_trade_tick(
    station_id: str, commodity: str, market_price: MarketPrice
) -> None:
    """Fire a real-time market update after a committed trade (respects the
    service's ~1s per-(station,commodity) batching). Defensive — purely
    cosmetic, never affects the trade outcome."""
    try:
        from src.services.realtime_market_service import get_market_service
        await get_market_service().publish_trade_tick(
            station_id=str(station_id),
            commodity=commodity,
            buy_price=market_price.buy_price,
            sell_price=market_price.sell_price,
            quantity=market_price.quantity,
        )
    except Exception:
        logger.debug("real-time trade tick publish skipped", exc_info=True)


async def _emit_transaction_completed(
    user_id: Any,
    tx_id: Any,
    station_id: str,
    commodity: str,
    units: int,
    total: int,
) -> None:
    """Push the canonical personal ``transaction_completed`` frame to the
    trading player after a buy/sell settles.

    Canon (SYSTEMS/realtime-bus.md: ``transaction_completed`` | personal |
    ``tx_id, station_id, commodity, units, total``; SYSTEMS/market-pricing.md:
    "unicast to the player's session"). Reuses the existing connection-manager
    ``send_personal_message`` idiom (the same personal-frame transport
    send_turn_pool_update / send_ship_status_change ride on), routing on the
    owning User's id — the key ``send_personal_message`` keys on.

    PERSONAL ONLY: send_personal_message delivers exclusively to this user's
    socket, so there is no cross-player leak. DEFENSIVE: the call site invokes
    this POST-COMMIT inside its own try/except, and this body swallows any
    failure — a WS hiccup (no socket, dead loop) must never disturb an
    already-committed trade."""
    try:
        if user_id is None:
            return
        from src.services.websocket_service import connection_manager
        await connection_manager.send_personal_message(
            str(user_id),
            {
                "type": "transaction_completed",
                "timestamp": datetime.now(UTC).isoformat(),
                "tx_id": str(tx_id),
                "station_id": str(station_id),
                "commodity": commodity,
                "units": units,
                "total": total,
            },
        )
    except Exception:
        logger.debug("transaction_completed WS push skipped", exc_info=True)


@router.post("/buy")
async def buy_resource(
    trade_request: TradeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Buy a resource from a station"""
    
    # Verify player is docked at this port
    if not current_player.is_docked:
        raise HTTPException(status_code=400, detail="You must be docked at a station to trade")

    # Get the station
    station = _get_station_or_404(db, trade_request.station_id)

    # Verify player is in the same sector as the station
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    # Populate MarketPrice rows from the commodities JSONB if missing
    _ensure_market_prices(db, station)

    # Honor the station's commodity trade flags: 'sells' means the station
    # sells this commodity TO players
    commodity_cfg = (station.commodities or {}).get(trade_request.resource_type)
    if commodity_cfg is not None and not commodity_cfg.get("sells", False):
        raise HTTPException(status_code=400, detail="Station does not sell this resource")

    # LOCK ORDER (global convention, deadlock contract): STATION row first,
    # then PLAYER rows — matching port_ownership_service / docking_service.
    # Locking player-then-station here while the ownership engine locked
    # station-then-player was an AB-BA deadlock. with_for_update() does NOT
    # refresh the already-loaded instance, so chain populate_existing();
    # that replaces the commodities JSONB attribute, so re-derive
    # commodity_cfg from the refreshed instance (the stale dict reference
    # would silently drop the quantity sync below).
    station = (
        db.query(Station)
        .filter(Station.id == station.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    commodity_cfg = (station.commodities or {}).get(trade_request.resource_type)

    # Lock player row to prevent race conditions on concurrent trades
    # (after the station lock — see lock-order note above)
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # Get current ship
    current_ship = db.query(Ship).filter(
        Ship.id == current_player.current_ship_id,
        Ship.owner_id == current_player.id
    ).first()
    if not current_ship:
        raise HTTPException(status_code=404, detail="No active ship found")

    # Get market price for this resource
    market_price = db.query(MarketPrice).filter(
        MarketPrice.station_id == trade_request.station_id,
        MarketPrice.commodity == trade_request.resource_type
    ).first()
    if not market_price:
        raise HTTPException(status_code=404, detail="Resource not available at this port")

    # Check if port has enough quantity
    if market_price.quantity < trade_request.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Station only has {market_price.quantity} units available"
        )

    # Players purchasing FROM the station pay sell_price (what the station
    # charges players). Charging buy_price here created a same-station
    # buy-low/sell-high arbitrage loop.
    #
    # Canon (trading.md#price-stacking-order, Max-blessed): the full per-unit
    # stack is rank discount × faction-rep × personal-rep × first-login × region
    # tariff × station lever, then the commodity hard [min, max] band as the
    # FINAL clamp. compute_effective_unit_price is the single source of truth for
    # this stack (shared with the haggle engine so haggle fair == posted price —
    # ADR-0079 point 6). The tariff/lever EFFECTIVE rates are surfaced in the
    # response below, so we still read them here.
    bonuses = RankingService.get_rank_bonuses(current_player.military_rank)
    _, tariff_rate_eff = compute_region_tariff_multiplier(db, station)
    _, lever_eff = compute_station_lever_multiplier(db, current_player, station)
    effective_buy_price = compute_effective_unit_price(
        db, current_player, station, trade_request.resource_type, "buy",
        market_price.sell_price,
    )

    # ADR-0079 haggling: if the player just ACCEPTED a numerical haggle for this
    # (station, commodity, BUY), the agreed per-unit price replaces the posted
    # price for THIS transaction (single-use; consumed so it can't be reused).
    # The agreed price was already clamped to [0.80, 1.20] x fair in the engine
    # (point 7); re-clamp to the commodity hard band for defence-in-depth. Point
    # 6: the haggle modifiers adjusted the acceptance BAND only — the fair price
    # the engine negotiated off already carried the trading.md stack, so we do NOT
    # re-apply the rank/rep modifiers above on top of the haggled price.
    try:
        from src.services.haggle_service import HaggleService
        haggled = HaggleService(db).consume_agreed_price(
            current_player, station.id, trade_request.resource_type, "buy"
        )
        if haggled is not None:
            effective_buy_price = clamp_to_commodity_band(
                trade_request.resource_type, int(round(haggled))
            )
    except Exception:
        logger.warning("haggle price consume failed (buy); using posted price", exc_info=True)

    # Calculate total cost
    total_cost = effective_buy_price * trade_request.quantity

    # REAL TAX (port-ownership canon): trades pay the station's tax_rate
    # into its treasury — but canon frames the tax as an OWNER lever, so
    # unowned (NPC) stations levy none. The station row is already locked
    # above, so concurrent trades can't lose treasury updates.
    tax_rate = (
        station.tax_rate if (station.owner_id is not None and station.tax_rate is not None) else 0.0
    )
    tax_amount = int(total_cost * tax_rate)
    total_with_tax = total_cost + tax_amount

    # Check if player has enough credits (goods + station trade tax)
    if current_player.credits < total_with_tax:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {total_with_tax}, have {current_player.credits}"
        )
    
    # Check ship cargo capacity
    # Cargo structure: {'used': X, 'capacity': Y, 'contents': {...}}
    cargo = current_ship.cargo or {'used': 0, 'capacity': 50, 'contents': {}}
    current_cargo_used = cargo.get('used', 0)
    cargo_capacity = cargo.get('capacity', 50)

    if current_cargo_used + trade_request.quantity > cargo_capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient cargo space. Have {cargo_capacity - current_cargo_used} free, need {trade_request.quantity}"
        )

    # Execute the trade
    try:
        # Update player credits (goods + tax); the tax is realized to the
        # station under the station lock taken above. ADR-0062 / port-ownership:
        # the trade tax is NOT 100% owner revenue — realize_port_revenue splits
        # it 40% defense / 30% owner / 30% operating (treasury_balance +
        # ownership JSONB buckets). Imported lazily and DEFENSIVE: any error
        # falls back to the old whole-tax-to-treasury behavior so a revenue
        # split hiccup can never fail the trade.
        current_player.credits -= total_with_tax
        if tax_amount > 0:
            try:
                from src.services import port_ownership_service
                port_ownership_service.realize_port_revenue(db, station, tax_amount)
            except Exception:
                logger.warning(
                    "realize_port_revenue failed (buy); falling back to treasury",
                    exc_info=True,
                )
                station.treasury_balance = (station.treasury_balance or 0) + tax_amount

        # Update ship cargo (using proper structure)
        if not current_ship.cargo:
            current_ship.cargo = {'used': 0, 'capacity': 50, 'contents': {}}

        contents = current_ship.cargo.get('contents', {})
        contents[trade_request.resource_type] = contents.get(trade_request.resource_type, 0) + trade_request.quantity
        current_ship.cargo['contents'] = contents
        current_ship.cargo['used'] = current_ship.cargo.get('used', 0) + trade_request.quantity
        flag_modified(current_ship, 'cargo')  # Mark JSONB as modified for SQLAlchemy

        # Update market quantity — both the MarketPrice row and the
        # commodities JSONB (the JSONB is the source TradingService rebuilds
        # prices from; leaving it stale resurrects sold stock on refresh)
        market_price.quantity -= trade_request.quantity
        market_price.last_transaction_at = datetime.now(UTC)
        if commodity_cfg is not None:
            commodity_cfg["quantity"] = max(0, commodity_cfg.get("quantity", 0) - trade_request.quantity)
            # ADR-0062 E-V4 demand split: player purchases raise the
            # PLAYER demand signal only (NPC trades feed the separate
            # npc_restock_demand key and never blend into this one).
            capacity = commodity_cfg.get("capacity", 0) or 0
            if capacity > 0:
                score = commodity_cfg.get("player_demand_score", 1.0)
                commodity_cfg["player_demand_score"] = round(
                    min(2.0, max(0.0, score + trade_request.quantity / capacity)), 4
                )
            flag_modified(station, 'commodities')

        # Recompute this commodity's market price from the post-trade supply
        # so the MarketPrice row reflects the trade's market impact. This sets
        # previous_sell_price (the alert baseline) and is fully defensive.
        alert_fired = _reprice_after_trade(
            db, station, market_price, trade_request.resource_type
        )

        # Create transaction record. The station_buy/sell_price snapshots
        # capture the station's prevailing prices at transaction time —
        # the takeover engine's hostile-pricing test reads them
        # (port_ownership_service._month_hostility).
        transaction = MarketTransaction(
            player_id=current_player.id,
            station_id=trade_request.station_id,
            transaction_type=TransactionType.BUY,
            commodity=trade_request.resource_type,
            quantity=trade_request.quantity,
            unit_price=effective_buy_price,
            total_value=total_cost,
            station_buy_price=market_price.buy_price,
            station_sell_price=market_price.sell_price,
            station_quantity=market_price.quantity,
            timestamp=datetime.now(UTC)
        )
        db.add(transaction)

        # Peaceful reputation gain (REPUTATION_TRIGGERS 'complete_trade', +1):
        # legitimate trade nudges Federation standing. Defensive — a reputation
        # hiccup must never fail the trade itself.
        try:
            from src.services.personal_reputation_service import PersonalReputationService
            PersonalReputationService(db).adjust_reputation(current_player.id, 1, "complete_trade")
        except Exception:
            logger.warning("complete_trade reputation nudge failed (buy)", exc_info=True)

        # WO-CD-2 — emergent FACTION rep for trade volume at a faction-flagged
        # port (CONCRETE-CANON, factions-and-teams.md): "Trade at a
        # Federation/Guild/Frontier/Fringe-flagged port | +1 / 5,000 cr". This
        # is the GENERIC trade trigger (TF/MG/FC/FA) — it fires on BUY as well
        # as SELL. The accumulator awards +1 per completed 5,000-cr block of
        # total_cost and carries the remainder forward (no over/under-pay). AM
        # is NOT here — its canon trigger is SELL ore to a refinery only (sell
        # path). Faction rep is DISJOINT from the +1 personal rep above
        # (ADR-0056 N-D1). Under the trade transaction (flush-only, pre-commit),
        # idempotent (one completed buy), defensive — never fails the trade.
        try:
            from src.services.emergent_reputation_service import (
                apply_trade_volume_rep,
                trade_volume_action_for_faction_name,
            )
            tv_action = trade_volume_action_for_faction_name(station.faction_affiliation)
            if tv_action is not None:
                apply_trade_volume_rep(
                    db, current_player, tv_action, total_cost,
                    {"sector_id": current_player.current_sector_id},
                )
        except Exception:
            logger.warning("emergent trade-volume faction rep failed (buy)", exc_info=True)

        # Award rank points for trading volume
        rank_awarded = None
        try:
            trade_points = RankingService.calculate_trading_points(total_cost)
            if trade_points > 0:
                ranking_service = RankingService(db)
                rank_awarded = ranking_service.award_rank_points(
                    current_player.id, trade_points, "trading_volume"
                )
        except Exception as e:
            logger.error("Failed to award rank points for buy trade: %s", e)

        # ARIA consciousness + medal hooks
        try:
            current_player.aria_total_interactions += 1
            # Check consciousness thresholds (50→L2, 150→L3, 400→L4, 1000→L5)
            thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
            for threshold, (level, multiplier) in thresholds.items():
                if current_player.aria_total_interactions >= threshold and current_player.aria_consciousness_level < level:
                    current_player.aria_consciousness_level = level
                    current_player.aria_bonus_multiplier = multiplier
            # Check trading medals
            trade_count = db.query(MarketTransaction).filter(
                MarketTransaction.player_id == current_player.id
            ).count()
            medal_service = MedalService(db)
            medal_service.check_trading_medals(current_player.id, trade_count, current_player.credits)
        except Exception as e:
            logger.error("Failed ARIA/medal hooks for buy trade: %s", e)

        # Record ARIA trade memory (best-effort, don't block trade)
        try:
            trade_memory = {
                "station_name": station.name if station else "Unknown",
                "action": "buy",
                "commodity": trade_request.resource_type,
                "quantity": trade_request.quantity,
                "total_value": total_cost,
            }
            if not current_player.settings:
                current_player.settings = {}
            pending = current_player.settings.get("pending_aria_memories", [])
            pending.append({"type": "trade", "data": trade_memory})
            # Keep only last 10 pending memories
            current_player.settings["pending_aria_memories"] = pending[-10:]
            flag_modified(current_player, "settings")
        except Exception as e:
            logger.debug("ARIA trade memory recording skipped: %s", e)

        db.commit()

        # Real-time market broadcast (post-commit, batched, defensive).
        await _publish_trade_tick(station.id, trade_request.resource_type, market_price)

        # Personal transaction_completed frame (post-commit, defensive — a WS
        # hiccup must never fail an already-committed trade). Personal only:
        # routes on the trading player's owning User id (no cross-player leak).
        await _emit_transaction_completed(
            current_player.user_id,
            transaction.id,
            trade_request.station_id,
            trade_request.resource_type,
            trade_request.quantity,
            total_cost,
        )

        response = {
            "message": f"Successfully bought {trade_request.quantity} units of {trade_request.resource_type}",
            "transaction": {
                "resource": trade_request.resource_type,
                "quantity": trade_request.quantity,
                "unit_price": effective_buy_price,
                "base_price": market_price.sell_price,
                "rank_discount_percent": bonuses["trading_discount_percent"],
                "total_cost": total_cost,
                "tax_rate": tax_rate,
                "tax": tax_amount,
                "total_with_tax": total_with_tax,
                # ADR-0062 E-D3 price-stack breakdown (tail factors).
                "tariff_rate": tariff_rate_eff,
                "price_lever": lever_eff,
                "remaining_credits": current_player.credits,
                "remaining_cargo_space": current_ship.cargo.get('capacity', 50) - current_ship.cargo.get('used', 0)
            }
        }
        if alert_fired:
            response["price_alert"] = True
        if rank_awarded and rank_awarded.get("success") and rank_awarded.get("points_awarded", 0) > 0:
            response["rank_points_awarded"] = rank_awarded["points_awarded"]
            if rank_awarded.get("promoted"):
                response["promoted_to"] = rank_awarded["new_rank"]
        return response

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Trade failed: {str(e)}")


@router.post("/sell")
async def sell_resource(
    trade_request: TradeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Sell a resource to a station"""

    # Verify player is docked at this port
    if not current_player.is_docked:
        raise HTTPException(status_code=400, detail="You must be docked at a station to trade")

    # Get the station
    station = _get_station_or_404(db, trade_request.station_id)

    # Verify player is in the same sector as the station
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    # Populate MarketPrice rows from the commodities JSONB if missing
    # (may COMMIT — must run before any row locks are taken)
    _ensure_market_prices(db, station)

    # Honor the station's commodity trade flags: 'buys' means the station
    # buys this commodity FROM players
    commodity_cfg = (station.commodities or {}).get(trade_request.resource_type)
    if commodity_cfg is not None and not commodity_cfg.get("buys", False):
        raise HTTPException(status_code=400, detail="Station does not buy this resource")

    # LOCK ORDER (global convention, deadlock contract): STATION row first,
    # then PLAYER rows — matching port_ownership_service / docking_service.
    # populate_existing() refreshes the already-loaded instance (plain
    # with_for_update() does not); it replaces the commodities JSONB
    # attribute, so re-derive commodity_cfg from the refreshed instance.
    station = (
        db.query(Station)
        .filter(Station.id == station.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    commodity_cfg = (station.commodities or {}).get(trade_request.resource_type)

    # Lock player row to prevent race conditions on concurrent trades
    # (after the station lock — see lock-order note above)
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # Get current ship
    current_ship = db.query(Ship).filter(
        Ship.id == current_player.current_ship_id,
        Ship.owner_id == current_player.id
    ).first()
    if not current_ship:
        raise HTTPException(status_code=404, detail="No active ship found")
    
    # Check if player has the resource
    # Cargo structure: {'used': X, 'capacity': Y, 'contents': {...}}
    cargo = current_ship.cargo or {'used': 0, 'capacity': 50, 'contents': {}}
    contents = cargo.get('contents', {})
    player_has = contents.get(trade_request.resource_type, 0)

    if player_has < trade_request.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"You don't have {trade_request.quantity} units of {trade_request.resource_type}. You have {player_has}."
        )
    
    # Get market price for this resource
    market_price = db.query(MarketPrice).filter(
        MarketPrice.station_id == trade_request.station_id,
        MarketPrice.commodity == trade_request.resource_type
    ).first()
    if not market_price:
        raise HTTPException(status_code=404, detail="Station doesn't trade this resource")
    
    # Players selling TO the station receive buy_price (what the station
    # pays players). Paying out sell_price here was the other half of the
    # same-station arbitrage loop.
    #
    # Canon (trading.md#price-stacking-order, Max-blessed): the full per-unit
    # payout stack flips the relationship direction (a favoured trader is paid
    # MORE) — rank bonus, then divide by player-rep/tariff/lever, then the
    # commodity hard band as the FINAL clamp. compute_effective_unit_price owns
    # this stack (shared with the haggle engine → haggle fair == posted payout,
    # ADR-0079 point 6). The tariff/lever EFFECTIVE rates feed the response below.
    bonuses = RankingService.get_rank_bonuses(current_player.military_rank)
    _, tariff_rate_eff = compute_region_tariff_multiplier(db, station)
    _, lever_eff = compute_station_lever_multiplier(db, current_player, station)
    effective_sell_price = compute_effective_unit_price(
        db, current_player, station, trade_request.resource_type, "sell",
        market_price.buy_price,
    )

    # ADR-0079 haggling: if the player just ACCEPTED a numerical haggle for this
    # (station, commodity, SELL), the agreed per-unit payout replaces the posted
    # payout for THIS transaction (single-use). Already clamped to [0.80, 1.20] x
    # fair in the engine; re-clamp to the commodity hard band. Point 6: do NOT
    # re-apply the rank/rep modifiers above on top of the haggled payout.
    try:
        from src.services.haggle_service import HaggleService
        haggled = HaggleService(db).consume_agreed_price(
            current_player, station.id, trade_request.resource_type, "sell"
        )
        if haggled is not None:
            effective_sell_price = clamp_to_commodity_band(
                trade_request.resource_type, int(round(haggled))
            )
    except Exception:
        logger.warning("haggle price consume failed (sell); using posted price", exc_info=True)

    # Calculate total earnings (gross, before station trade tax)
    total_earnings = effective_sell_price * trade_request.quantity

    # REAL TAX (port-ownership canon): the station's tax_rate is withheld
    # from sale proceeds and credited to its treasury — but canon frames
    # the tax as an OWNER lever, so unowned (NPC) stations levy none. The
    # station row is already locked above (station-first lock order), so
    # concurrent trades can't lose treasury updates.
    tax_rate = (
        station.tax_rate if (station.owner_id is not None and station.tax_rate is not None) else 0.0
    )
    tax_amount = int(total_earnings * tax_rate)
    net_earnings = total_earnings - tax_amount

    # Execute the trade
    try:
        # Update player credits (net of tax); the withheld tax is realized to
        # the station under the station lock taken above. ADR-0062 / port-
        # ownership: split 40/30/30 via realize_port_revenue, NOT 100% to the
        # owner treasury. DEFENSIVE — any error falls back to the old whole-tax
        # behavior so a revenue split hiccup can never fail the trade.
        current_player.credits += net_earnings
        if tax_amount > 0:
            try:
                from src.services import port_ownership_service
                port_ownership_service.realize_port_revenue(db, station, tax_amount)
            except Exception:
                logger.warning(
                    "realize_port_revenue failed (sell); falling back to treasury",
                    exc_info=True,
                )
                station.treasury_balance = (station.treasury_balance or 0) + tax_amount

        # Update ship cargo (using proper structure)
        if not current_ship.cargo:
            current_ship.cargo = {'used': 0, 'capacity': 50, 'contents': {}}

        contents = current_ship.cargo.get('contents', {})
        contents[trade_request.resource_type] = contents.get(trade_request.resource_type, 0) - trade_request.quantity
        if contents[trade_request.resource_type] <= 0:
            del contents[trade_request.resource_type]
        current_ship.cargo['contents'] = contents
        current_ship.cargo['used'] = max(0, current_ship.cargo.get('used', 0) - trade_request.quantity)
        flag_modified(current_ship, 'cargo')  # Mark JSONB as modified for SQLAlchemy

        # Update market quantity — keep the commodities JSONB in sync (it is
        # the source TradingService rebuilds prices from)
        market_price.quantity += trade_request.quantity
        market_price.last_transaction_at = datetime.now(UTC)
        if commodity_cfg is not None:
            commodity_cfg["quantity"] = commodity_cfg.get("quantity", 0) + trade_request.quantity
            # ADR-0062 E-V4 demand split: player supply satisfies player
            # demand — lower the PLAYER signal only.
            capacity = commodity_cfg.get("capacity", 0) or 0
            if capacity > 0:
                score = commodity_cfg.get("player_demand_score", 1.0)
                commodity_cfg["player_demand_score"] = round(
                    min(2.0, max(0.0, score - trade_request.quantity / capacity)), 4
                )
            flag_modified(station, 'commodities')

        # Recompute price from post-trade supply (sets the alert baseline) and
        # auto-fire a PriceAlert if the threshold was crossed. Defensive.
        alert_fired = _reprice_after_trade(
            db, station, market_price, trade_request.resource_type
        )

        # Create transaction record. The station_buy/sell_price snapshots
        # capture the station's prevailing prices at transaction time —
        # the takeover engine's hostile-pricing test reads them
        # (port_ownership_service._month_hostility).
        transaction = MarketTransaction(
            player_id=current_player.id,
            station_id=trade_request.station_id,
            transaction_type=TransactionType.SELL,
            commodity=trade_request.resource_type,
            quantity=trade_request.quantity,
            unit_price=effective_sell_price,
            total_value=total_earnings,
            station_buy_price=market_price.buy_price,
            station_sell_price=market_price.sell_price,
            station_quantity=market_price.quantity,
            timestamp=datetime.now(UTC)
        )
        db.add(transaction)

        # Peaceful reputation gain (REPUTATION_TRIGGERS 'complete_trade', +1):
        # legitimate trade nudges Federation standing. Defensive — a reputation
        # hiccup must never fail the trade itself.
        try:
            from src.services.personal_reputation_service import PersonalReputationService
            PersonalReputationService(db).adjust_reputation(current_player.id, 1, "complete_trade")
        except Exception:
            logger.warning("complete_trade reputation nudge failed (sell)", exc_info=True)

        # WO-CD-2 — emergent FACTION rep for trade volume at a faction-flagged
        # port (CONCRETE-CANON, factions-and-teams.md). Two distinct canon
        # triggers can fire on a SELL:
        #   1) The GENERIC trade trigger (TF/MG/FC/FA): "Trade at a
        #      Federation/Guild/Frontier/Fringe-flagged port | +1 / 5,000 cr"
        #      — accrues total_earnings toward 5,000-cr blocks (+1 / block).
        #   2) AM ore→refinery: "Sell raw ore to an AM-flagged refinery |
        #      +2 / 5,000 cr" (double-weighted) — fires ONLY when this is an ORE
        #      sell at an Astral-Mining-flagged station whose
        #      services['refining_facility'] is true (the canon "refinery"
        #      qualifier). +2 / block.
        # A station is flagged for at most one faction, so at most one of these
        # branches fires on any given sell. Faction rep is DISJOINT from the +1
        # personal rep above (ADR-0056 N-D1). Under the trade transaction
        # (flush-only, pre-commit), idempotent (one completed sell), defensive.
        try:
            from src.services.emergent_reputation_service import (
                apply_trade_volume_rep,
                trade_volume_action_for_faction_name,
            )
            tv_ctx = {"sector_id": current_player.current_sector_id}
            tv_action = trade_volume_action_for_faction_name(station.faction_affiliation)
            if tv_action is not None:
                apply_trade_volume_rep(
                    db, current_player, tv_action, total_earnings, tv_ctx
                )
            elif (
                trade_request.resource_type == "ore"
                and station.faction_affiliation == "Astral Mining Consortium"
                and (station.services or {}).get("refining_facility", False)
            ):
                # AM ore→refinery (+2 / 5,000 cr). The faction match uses the
                # same Faction.name convention as the rest of the trade stack.
                apply_trade_volume_rep(
                    db, current_player, "TRADE_VOLUME_AM_ORE", total_earnings, tv_ctx
                )
        except Exception:
            logger.warning("emergent trade-volume faction rep failed (sell)", exc_info=True)

        # Award rank points for trading volume
        rank_awarded = None
        try:
            trade_points = RankingService.calculate_trading_points(total_earnings)
            if trade_points > 0:
                ranking_service = RankingService(db)
                rank_awarded = ranking_service.award_rank_points(
                    current_player.id, trade_points, "trading_volume"
                )
        except Exception as e:
            logger.error("Failed to award rank points for sell trade: %s", e)

        # ARIA consciousness + medal hooks
        try:
            current_player.aria_total_interactions += 1
            # Check consciousness thresholds (50→L2, 150→L3, 400→L4, 1000→L5)
            thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
            for threshold, (level, multiplier) in thresholds.items():
                if current_player.aria_total_interactions >= threshold and current_player.aria_consciousness_level < level:
                    current_player.aria_consciousness_level = level
                    current_player.aria_bonus_multiplier = multiplier
            # Check trading medals
            trade_count = db.query(MarketTransaction).filter(
                MarketTransaction.player_id == current_player.id
            ).count()
            medal_service = MedalService(db)
            medal_service.check_trading_medals(current_player.id, trade_count, current_player.credits)
        except Exception as e:
            logger.error("Failed ARIA/medal hooks for sell trade: %s", e)

        # Record ARIA trade memory (best-effort, don't block trade)
        try:
            trade_memory = {
                "station_name": station.name if station else "Unknown",
                "action": "sell",
                "commodity": trade_request.resource_type,
                "quantity": trade_request.quantity,
                "total_value": total_earnings,
            }
            if not current_player.settings:
                current_player.settings = {}
            pending = current_player.settings.get("pending_aria_memories", [])
            pending.append({"type": "trade", "data": trade_memory})
            # Keep only last 10 pending memories
            current_player.settings["pending_aria_memories"] = pending[-10:]
            flag_modified(current_player, "settings")
        except Exception as e:
            logger.debug("ARIA trade memory recording skipped: %s", e)

        db.commit()

        # Real-time market broadcast (post-commit, batched, defensive).
        await _publish_trade_tick(station.id, trade_request.resource_type, market_price)

        # Personal transaction_completed frame (post-commit, defensive — a WS
        # hiccup must never fail an already-committed trade). Personal only:
        # routes on the trading player's owning User id (no cross-player leak).
        # ``total`` carries the gross transaction value (MarketTransaction
        # total_value), matching the buy side's total_cost.
        await _emit_transaction_completed(
            current_player.user_id,
            transaction.id,
            trade_request.station_id,
            trade_request.resource_type,
            trade_request.quantity,
            total_earnings,
        )

        remaining = current_ship.cargo.get('contents', {}).get(trade_request.resource_type, 0)
        response = {
            "message": f"Successfully sold {trade_request.quantity} units of {trade_request.resource_type}",
            "transaction": {
                "resource": trade_request.resource_type,
                "quantity": trade_request.quantity,
                "unit_price": effective_sell_price,
                "base_price": market_price.buy_price,
                "rank_bonus_percent": bonuses["trading_discount_percent"],
                "total_earnings": total_earnings,
                "tax_rate": tax_rate,
                "tax": tax_amount,
                "net_earnings": net_earnings,
                # ADR-0062 E-D3 price-stack breakdown (tail factors).
                "tariff_rate": tariff_rate_eff,
                "price_lever": lever_eff,
                "new_credits": current_player.credits,
                "remaining_cargo": remaining
            }
        }
        if alert_fired:
            response["price_alert"] = True
        if rank_awarded and rank_awarded.get("success") and rank_awarded.get("points_awarded", 0) > 0:
            response["rank_points_awarded"] = rank_awarded["points_awarded"]
            if rank_awarded.get("promoted"):
                response["promoted_to"] = rank_awarded["new_rank"]
        return response

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Trade failed: {str(e)}")


@router.get("/market/{station_id}", response_model=MarketInfoResponse)
async def get_market_info(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Get market information for a specific port"""

    # Get the station
    station = _get_station_or_404(db, station_id)

    # Populate MarketPrice rows from the commodities JSONB if missing
    _ensure_market_prices(db, station)

    # Get all market prices for this port
    market_prices = db.query(MarketPrice).filter(MarketPrice.station_id == station.id).all()

    # Market prices are player-effective (rank bonuses applied) so client
    # previews match charges: mirror the int()/max(1, ...) math the buy and
    # sell handlers use. sell_price is what THIS player pays the station
    # (rank discount applied); buy_price is what the station pays THIS
    # player (rank bonus applied).
    bonuses = RankingService.get_rank_bonuses(current_player.military_rank)
    rank_rate = bonuses["trading_discount_percent"] / 100.0

    # Format resources, carrying the station's trade-direction flags so the
    # client can show only actionable buy/sell options
    station_commodities = station.commodities or {}
    resources = {}
    for price in market_prices:
        cfg = station_commodities.get(price.commodity) or {}
        resources[price.commodity] = {
            "quantity": price.quantity,
            "buy_price": max(1, int(price.buy_price * (1 + rank_rate))),
            "sell_price": max(1, int(price.sell_price * (1 - rank_rate))),
            "station_buys": bool(cfg.get("buys", True)),
            "station_sells": bool(cfg.get("sells", True)),
            # ADR-0062 E-V4: the player-facing demand indicator reads the
            # PLAYER demand signal only — NPC trader activity (the
            # npc_restock_demand key) is never surfaced here.
            "player_demand_score": float(cfg.get("player_demand_score", 1.0)),
            "last_updated": price.updated_at.isoformat() if price.updated_at else None
        }
    
    return MarketInfoResponse(
        resources=resources,
        port={
            "id": str(station.id),
            "name": station.name,
            "type": station.type,
            "faction": station.faction_affiliation,
            # EFFECTIVE rate: tax is an owner lever (port-ownership canon) —
            # unowned stations charge nothing, so display nothing.
            "tax_rate": station.tax_rate if (station.owner_id is not None and station.tax_rate is not None) else 0.0,
            "station_class": str(station.station_class.value) if station.station_class else None,
            "is_spacedock": bool(station.is_spacedock),
            "trade_volume": station.trade_volume,
            "trader_personality_type": (station.trader_personality or {}).get("type")
        }
    )


@router.post("/dock")
async def dock_at_station(
    dock_request: StationDockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Dock at a station"""

    # Define docking turn cost
    DOCKING_TURN_COST = 1

    # LOCK ORDER (global convention, deadlock contract): STATION row first,
    # then PLAYER rows. docking_service.acquire() re-locks this same station
    # row later in the transaction (already held — no extra wait); taking the
    # player lock first while acquire() took the station lock was an AB-BA
    # deadlock against the trade/ownership paths.
    station = (
        db.query(Station)
        .filter(Station.id == dock_request.station_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Lock player row to prevent concurrent turn deduction races
    # (after the station lock — see lock-order note above)
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # ADR-0004: continuous lazy regen — refill the pool for real elapsed time
    # inside the row lock, BEFORE the affordability check, so docking is never
    # rejected on a stale-low balance.
    regenerate_turns(db, current_player)

    # Verify player is in the same sector as the station
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    # Check if already docked
    if current_player.is_docked:
        raise HTTPException(status_code=400, detail="You are already docked at a station")
    
    # Check if landed on a planet (can't dock while landed)
    if current_player.is_landed:
        raise HTTPException(status_code=400, detail="You must leave the planet before docking at a station")

    # A hull fused into a warp gate focus cannot maneuver to dock
    current_ship = db.query(Ship).filter(Ship.id == current_player.current_ship_id).first()
    if current_ship and current_ship.status == ShipStatus.HARMONIZING:
        raise HTTPException(
            status_code=400,
            detail="Your ship is harmonizing into a warp gate focus and cannot dock — cancel the anchor first"
        )

    # Check if player has enough turns
    if current_player.turns < DOCKING_TURN_COST:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient turns. Need {DOCKING_TURN_COST} turn(s), have {current_player.turns}"
        )

    # Docking fee (canon: fees fund the station treasury). Validated after the
    # turn check, in addition to the 1-turn dock cost.
    docking_fee = docking_service.docking_fee_for(station)
    if current_player.credits < docking_fee:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits for the {docking_fee}cr docking fee. Have {current_player.credits}"
        )

    # Claim a transient slip. acquire() locks the station row to serialize
    # slot grants; occupancy rows are the source of truth for slip usage.
    slip_result = docking_service.acquire(
        db, station, current_player, ship_id=current_player.current_ship_id
    )

    if slip_result["status"] != "granted":
        # All transient slips taken (or the free slot belongs to the queue
        # head). Auto-enqueue the requester so they hold a FIFO position,
        # then report the choices: wait, or pay 5x the fee to bump.
        queue_position = slip_result.get("position")
        if queue_position is None:
            db.add(DockingQueueEntry(station_id=station.id, player_id=current_player.id))
            queue_position = slip_result["queue_length"] + 1
        db.commit()
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    f"All transient docking slips at {station.name} are occupied "
                    f"({slip_result['occupied']}/{slip_result['capacity']})"
                    if slip_result['occupied'] >= slip_result['capacity']
                    else (
                        f"A slip is free at {station.name} but the docking queue "
                        f"has priority ({slip_result['occupied']}/{slip_result['capacity']} occupied)"
                    )
                ),
                "slips": {
                    "capacity": slip_result["capacity"],
                    "occupied": slip_result["occupied"],
                },
                "queue_position": queue_position,
                "bumpable": slip_result["bumpable"],
                "bump_cost": docking_fee * docking_service.BUMP_COST_MULTIPLIER,
            },
        )

    try:
        # Update player status
        current_player.is_docked = True
        current_player.current_port_id = dock_request.station_id

        # Deduct turns for docking
        spend_turns(current_player, DOCKING_TURN_COST)

        # Charge the docking fee; fees accrue to the station treasury.
        # The station row is already locked by acquire() — one session,
        # single commit.
        current_player.credits -= docking_fee
        station.treasury_balance = (station.treasury_balance or 0) + docking_fee
        slip_result["occupancy"].fee_paid = docking_fee

        db.commit()

        return {
            "message": f"Successfully docked at {station.name}",
            "turn_cost": DOCKING_TURN_COST,
            "turns_remaining": current_player.turns,
            "docking_fee": docking_fee,
            "credits_remaining": current_player.credits,
            "slips": {
                "capacity": slip_result["capacity"],
                "occupied": slip_result["occupied"],
            },
            "station": {
                "id": str(station.id),
                "name": station.name,
                "type": station.type,
                "faction": station.faction_affiliation,
                "services": station.services or {}
            }
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Docking failed: {str(e)}")


@router.post("/undock")
async def undock_from_port(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Undock from current port"""

    # Define undocking turn cost
    UNDOCKING_TURN_COST = 1

    # Lock player row to prevent concurrent turn deduction races
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # ADR-0004: continuous lazy regen before the affordability check.
    regenerate_turns(db, current_player)

    if not current_player.is_docked:
        raise HTTPException(status_code=400, detail="You are not currently docked at a station")

    # Check if player has enough turns
    if current_player.turns < UNDOCKING_TURN_COST:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient turns. Need {UNDOCKING_TURN_COST} turn(s), have {current_player.turns}"
        )
    
    try:
        # Free the transient slip in the same transaction. Tolerates a
        # missing occupancy row (players docked before the slip system
        # shipped never held one).
        docking_service.release(db, None, current_player)

        # Update player status
        current_player.is_docked = False
        current_player.current_port_id = None

        # ADR-0079 haggling: a REJECT hard-locks a commodity "for the docking
        # session", and in-flight sessions don't carry across visits — clear both
        # on undock (re-entry cooldowns are real-time and intentionally persist).
        # Defensive: a haggle-state hiccup must never fail an undock.
        try:
            from src.services.haggle_service import clear_docking_session_haggles
            clear_docking_session_haggles(current_player)
        except Exception:
            logger.warning("clearing docking-session haggle state failed", exc_info=True)

        # Deduct turns for undocking
        spend_turns(current_player, UNDOCKING_TURN_COST)

        db.commit()

        return {
            "message": "Successfully undocked from port",
            "turn_cost": UNDOCKING_TURN_COST,
            "turns_remaining": current_player.turns
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Undocking failed: {str(e)}")


@router.get("/stations/{station_id}/slips")
async def get_station_slips(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Transient docking slip availability for a station.

    No docking required — powers the pre-dock UI:
    "Transient slips: 11/12 occupied. Estimated wait: ~N min".
    """
    station = _get_station_or_404(db, station_id)

    capacity = docking_service.slip_capacity_for(station)
    occupancies = db.query(DockingSlipOccupancy).filter(
        DockingSlipOccupancy.station_id == station.id,
        DockingSlipOccupancy.slip_class == "transient"
    ).all()
    occupied = len(occupancies)

    queue = db.query(DockingQueueEntry).filter(
        DockingQueueEntry.station_id == station.id
    ).order_by(DockingQueueEntry.created_at.asc()).all()
    my_queue_position = next(
        (idx + 1 for idx, entry in enumerate(queue) if entry.player_id == current_player.id),
        None
    )

    fee = docking_service.docking_fee_for(station)

    # Estimated wait (canon UX promise): wall-clock minutes until the
    # longest-tenured occupant crosses the 4h bumpable threshold — the
    # earliest moment a slip can realistically be forced free. 0 when free.
    estimated_wait_minutes = 0
    if occupied >= capacity and occupancies:
        from src.core.game_time import GAME_TIME_SCALE, canonical_hours_since
        max_tenure = max(canonical_hours_since(o.docked_at) for o in occupancies)
        remaining_canonical_h = max(0.0, docking_service.BUMP_MIN_TENURE_HOURS - max_tenure)
        estimated_wait_minutes = int(round(remaining_canonical_h * 60 / GAME_TIME_SCALE))

    return {
        "capacity": capacity,
        "occupied": occupied,
        "free": max(capacity - occupied, 0),
        "estimated_wait_minutes": estimated_wait_minutes,
        "fee": fee,
        "bump_cost": fee * docking_service.BUMP_COST_MULTIPLIER,
        "queue_length": len(queue),
        "my_queue_position": my_queue_position,
        "occupants_bumpable_count": sum(
            1 for occ in occupancies if docking_service.is_bumpable(occ)
        )
    }


@router.post("/stations/{station_id}/slips/bump")
async def bump_docking_slip(
    station_id: str,
    bump_request: SlipBumpRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Pay 5x the docking fee to evict a long-tenured slip occupant
    (>= 4 canonical hours of tenure) and dock in the freed slip.

    Bump + dock execute in one transaction; the response mirrors the
    /dock success shape plus the evicted occupant's info.
    """
    # Define docking turn cost (the bump includes a normal dock)
    DOCKING_TURN_COST = 1

    # Get the station
    station = _get_station_or_404(db, station_id)

    # Same pre-flight checks as /dock. NOTE: the player row is deliberately
    # NOT locked here — docking_service.bump locks the station row first,
    # then BOTH player rows in ascending player-id order (deadlock
    # avoidance; see docking_service lock-ordering notes).
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    if current_player.is_docked:
        raise HTTPException(status_code=400, detail="You are already docked at a station")

    if current_player.is_landed:
        raise HTTPException(status_code=400, detail="You must leave the planet before docking at a station")

    if current_player.turns < DOCKING_TURN_COST:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient turns. Need {DOCKING_TURN_COST} turn(s), have {current_player.turns}"
        )

    import uuid as _uuid
    try:
        occupant_uuid = _uuid.UUID(str(bump_request.occupant_player_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="That occupant does not hold a slip at this station")

    try:
        bump_result = docking_service.bump(db, station, current_player, occupant_uuid)
    except docking_service.BumpError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    try:
        # ADR-0004: regen now that bump holds the player row lock, before the
        # affordability re-check.
        regenerate_turns(db, current_player)
        # Re-check turns now that the player row is locked (bump locked it),
        # then dock the bumper in the freed slip.
        if current_player.turns < DOCKING_TURN_COST:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient turns. Need {DOCKING_TURN_COST} turn(s), have {current_player.turns}"
            )

        current_player.is_docked = True
        current_player.current_port_id = station.id
        spend_turns(current_player, DOCKING_TURN_COST)

        db.commit()

        # Notify the evicted occupant only AFTER the eviction is durable
        evicted_user_id = bump_result["evicted"].pop("_notify_user_id", None)
        if evicted_user_id is not None:
            docking_service._notify_bumped(evicted_user_id, station.name)

        return {
            "message": f"Successfully bumped an occupant and docked at {station.name}",
            "turn_cost": DOCKING_TURN_COST,
            "turns_remaining": current_player.turns,
            "docking_fee": bump_result["cost"],
            "credits_remaining": current_player.credits,
            "evicted": bump_result["evicted"],
            "slips": {
                "capacity": bump_result["capacity"],
                "occupied": bump_result["occupied"],
            },
            "station": {
                "id": str(station.id),
                "name": station.name,
                "type": station.type,
                "faction": station.faction_affiliation,
                "services": station.services or {}
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Docking failed: {str(e)}")


@router.post("/mooring/long-term")
async def acquire_long_term_mooring(
    mooring_request: LongTermMooringRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Reserve a long-term mooring slip at a station.

    Canon (FEATURES/economy/docking-slips §Long-term mooring): long-term slips
    are a SEPARATE pool from transient slips and do NOT participate in the bump
    mechanism. The player pays `days` * 200 cr upfront (1–30 days). Unlike a
    transient dock this is a multi-day parking reservation — it costs no turns
    and does not set `is_docked`; the ship occupies a long-term slip until
    released.

    The service owns reputation gating, capacity, credit deduction, and the
    station treasury credit; this route validates the duration bound, locks the
    station row first (deadlock contract), and maps the service status to HTTP.
    """
    # LOCK ORDER (global convention): STATION row first, then PLAYER rows.
    # acquire_long_term() re-locks this same station row (already held — no
    # extra wait) and locks the player row to deduct credits, matching the
    # transient /dock path's lock ordering.
    station = (
        db.query(Station)
        .filter(Station.id == mooring_request.station_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # A long-term slip is claimed at the station the player is physically at,
    # mirroring the same-sector requirement of the transient /dock route.
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    try:
        result = docking_service.acquire_long_term(
            db,
            station,
            current_player,
            days=mooring_request.days,
            ship_id=current_player.current_ship_id,
        )

        status = result["status"]
        if status == "granted":
            occupancy = result["occupancy"]
            db.commit()
            return {
                "message": (
                    f"Long-term mooring secured at {station.name} "
                    f"for {result['days']} day(s)"
                ),
                "days": result["days"],
                "fee_paid": result["fee_paid"],
                "credits_remaining": current_player.credits,
                "slip": {
                    "id": str(occupancy.id),
                    "slip_class": occupancy.slip_class,
                    "docked_at": occupancy.docked_at.isoformat() if occupancy.docked_at else None,
                },
                "slips": {
                    "capacity": result["capacity"],
                    "occupied": result["occupied"],
                },
                "station": {
                    "id": str(station.id),
                    "name": station.name,
                    "type": station.type,
                    "faction": station.faction_affiliation,
                },
            }

        # Non-granted outcomes: roll back any locks/changes, map to HTTP.
        db.rollback()
        if status == "invalid_days":
            raise HTTPException(status_code=400, detail=result["detail"])
        if status == "unavailable":
            raise HTTPException(status_code=400, detail=result["detail"])
        if status == "reputation_denied":
            raise HTTPException(status_code=403, detail=result["detail"])
        if status == "insufficient_credits":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Insufficient credits for long-term mooring. "
                    f"Need {result['need']}, have {result['have']}"
                ),
            )
        if status == "full":
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        f"All long-term mooring slips at {station.name} are occupied "
                        f"({result['occupied']}/{result['capacity']})"
                    ),
                    "slips": {
                        "capacity": result["capacity"],
                        "occupied": result["occupied"],
                    },
                },
            )
        # "error" or any unforeseen status
        raise HTTPException(
            status_code=500,
            detail=result.get("detail", "Long-term mooring failed"),
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Long-term mooring failed: {str(e)}")


@router.post("/mooring/long-term/release")
async def release_long_term_mooring(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Release the caller's long-term mooring slip.

    Canon: no fee refund — the pre-paid fee is consumed on grant. Tolerates a
    missing slip (returns released=false). Like the service method, this owns
    the transaction; it costs no turns, mirroring the parking-reservation
    nature of long-term mooring.
    """
    try:
        released = docking_service.release_long_term(db, None, current_player)
        db.commit()

        if not released:
            return {
                "message": "You hold no long-term mooring slip to release",
                "released": False,
            }

        return {
            "message": "Long-term mooring released",
            "released": True,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Releasing long-term mooring failed: {str(e)}")


@router.get("/history")
async def get_trading_history(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player)
):
    """Get player's trading history"""
    
    transactions = db.query(MarketTransaction).filter(
        MarketTransaction.player_id == current_player.id
    ).order_by(
        MarketTransaction.timestamp.desc()
    ).limit(limit).all()
    
    history = []
    for tx in transactions:
        station = db.query(Station).filter(Station.id == tx.station_id).first()
        history.append({
            "id": str(tx.id),
            "type": tx.transaction_type.value,
            "commodity": tx.commodity,
            "quantity": tx.quantity,
            "unit_price": tx.unit_price,
            "total_value": tx.total_value,
            "profit_margin": tx.profit_margin,
            "timestamp": tx.timestamp.isoformat(),
            "station_name": station.name if station else "Unknown Station"
        })
    
    return {
        "transactions": history,
        "total_transactions": len(history)
    }


# ---------------------------------------------------------------------------
# Legacy aliases — tests/unit/test_docking_turns.py (written before the
# port -> station rename) imports these names from this module. Keep them
# importable so the suite collects.
# ---------------------------------------------------------------------------
PortDockRequest = StationDockRequest
dock_at_port = dock_at_station