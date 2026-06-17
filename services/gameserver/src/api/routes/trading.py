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
from src.services.trading_service import TradingService
from src.services.ranking_service import RankingService
from src.services.medal_service import MedalService
from src.services import docking_service
from src.services.turn_service import spend_turns

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
    bonuses = RankingService.get_rank_bonuses(current_player.military_rank)
    discount_pct = bonuses["trading_discount_percent"] / 100.0
    discounted_price = market_price.sell_price * (1 - discount_pct)
    effective_buy_price = max(1, int(discounted_price))  # Floor at 1 credit

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
        # Update player credits (goods + tax); the tax accrues to the
        # station treasury under the station lock taken above
        current_player.credits -= total_with_tax
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
                "remaining_credits": current_player.credits,
                "remaining_cargo_space": current_ship.cargo.get('capacity', 50) - current_ship.cargo.get('used', 0)
            }
        }
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
    bonuses = RankingService.get_rank_bonuses(current_player.military_rank)
    bonus_pct = bonuses["trading_discount_percent"] / 100.0
    boosted_price = market_price.buy_price * (1 + bonus_pct)
    effective_sell_price = max(1, int(boosted_price))

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
        # Update player credits (net of tax); the withheld tax accrues to
        # the station treasury under the station lock taken above
        current_player.credits += net_earnings
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
                "new_credits": current_player.credits,
                "remaining_cargo": remaining
            }
        }
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