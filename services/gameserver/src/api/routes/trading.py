from fastapi import APIRouter, Depends, HTTPException
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
from src.models.ship import Ship
from src.models.market_transaction import MarketTransaction, MarketPrice, TransactionType
from src.services.trading_service import TradingService
from src.services.ranking_service import RankingService
from src.services.medal_service import MedalService

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading", tags=["trading"])


class TradeRequest(BaseModel):
    station_id: str
    resource_type: str
    quantity: int = Field(..., gt=0, le=100000, description="Must be between 1 and 100,000")


class StationDockRequest(BaseModel):
    station_id: str


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
    """Bridge the station's commodities JSONB into MarketPrice rows.

    Galaxy imports (BANG) stock stations only via the commodities JSONB;
    the market/buy/sell endpoints read MarketPrice rows, which otherwise
    never exist — every market in the universe reads empty. Populate them
    lazily on first access; TradingService keeps them current afterwards.
    """
    if not station.commodities:
        return
    has_rows = db.query(MarketPrice.id).filter(
        MarketPrice.station_id == station.id
    ).first()
    if has_rows:
        return
    TradingService(db).update_market_prices(station.id)
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

    # Lock player row to prevent race conditions on concurrent trades
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

    # Check if player has enough credits
    if current_player.credits < total_cost:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient credits. Need {total_cost}, have {current_player.credits}"
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
        # Update player credits
        current_player.credits -= total_cost

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
            flag_modified(station, 'commodities')

        # Create transaction record
        transaction = MarketTransaction(
            player_id=current_player.id,
            station_id=trade_request.station_id,
            transaction_type=TransactionType.BUY,
            commodity=trade_request.resource_type,
            quantity=trade_request.quantity,
            unit_price=effective_buy_price,
            total_value=total_cost,
            timestamp=datetime.now(UTC)
        )
        db.add(transaction)

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

    # Lock player row to prevent race conditions on concurrent trades
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # Get the station
    station = _get_station_or_404(db, trade_request.station_id)

    # Verify player is in the same sector as the station
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")

    # Populate MarketPrice rows from the commodities JSONB if missing
    _ensure_market_prices(db, station)

    # Honor the station's commodity trade flags: 'buys' means the station
    # buys this commodity FROM players
    commodity_cfg = (station.commodities or {}).get(trade_request.resource_type)
    if commodity_cfg is not None and not commodity_cfg.get("buys", False):
        raise HTTPException(status_code=400, detail="Station does not buy this resource")
    
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

    # Calculate total earnings
    total_earnings = effective_sell_price * trade_request.quantity

    # Execute the trade
    try:
        # Update player credits
        current_player.credits += total_earnings

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
            flag_modified(station, 'commodities')

        # Create transaction record
        transaction = MarketTransaction(
            player_id=current_player.id,
            station_id=trade_request.station_id,
            transaction_type=TransactionType.SELL,
            commodity=trade_request.resource_type,
            quantity=trade_request.quantity,
            unit_price=effective_sell_price,
            total_value=total_earnings,
            timestamp=datetime.now(UTC)
        )
        db.add(transaction)

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

    # Format resources, carrying the station's trade-direction flags so the
    # client can show only actionable buy/sell options
    station_commodities = station.commodities or {}
    resources = {}
    for price in market_prices:
        cfg = station_commodities.get(price.commodity) or {}
        resources[price.commodity] = {
            "quantity": price.quantity,
            "buy_price": price.buy_price,
            "sell_price": price.sell_price,
            "station_buys": bool(cfg.get("buys", True)),
            "station_sells": bool(cfg.get("sells", True)),
            "last_updated": price.updated_at.isoformat() if price.updated_at else None
        }
    
    return MarketInfoResponse(
        resources=resources,
        port={
            "id": str(station.id),
            "name": station.name,
            "type": station.type,
            "faction": station.faction_affiliation,
            "tax_rate": getattr(station, 'tax_rate', 0.1),  # Default 10% tax if not set
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

    # Lock player row to prevent concurrent turn deduction races
    current_player = db.query(Player).filter(Player.id == current_player.id).with_for_update().first()

    # Get the station
    station = db.query(Station).filter(Station.id == dock_request.station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Verify player is in the same sector as the station
    if current_player.current_sector_id != station.sector_id:
        raise HTTPException(status_code=400, detail="You must be in the same sector as the station")
    
    # Check if already docked
    if current_player.is_docked:
        raise HTTPException(status_code=400, detail="You are already docked at a station")
    
    # Check if landed on a planet (can't dock while landed)
    if current_player.is_landed:
        raise HTTPException(status_code=400, detail="You must leave the planet before docking at a station")
    
    # Check if player has enough turns
    if current_player.turns < DOCKING_TURN_COST:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient turns. Need {DOCKING_TURN_COST} turn(s), have {current_player.turns}"
        )
    
    try:
        # Update player status
        current_player.is_docked = True
        current_player.current_port_id = dock_request.station_id
        
        # Deduct turns for docking
        current_player.turns -= DOCKING_TURN_COST
        
        db.commit()
        
        return {
            "message": f"Successfully docked at {station.name}",
            "turn_cost": DOCKING_TURN_COST,
            "turns_remaining": current_player.turns,
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
        # Update player status
        current_player.is_docked = False
        current_player.current_port_id = None
        
        # Deduct turns for undocking
        current_player.turns -= UNDOCKING_TURN_COST
        
        db.commit()
        
        return {
            "message": "Successfully undocked from port",
            "turn_cost": UNDOCKING_TURN_COST,
            "turns_remaining": current_player.turns
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Undocking failed: {str(e)}")


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