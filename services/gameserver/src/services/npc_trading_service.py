"""NPC Trading Service — TRADER archetype NPCs as full market actors.

Canon (SYSTEMS/npc-lifecycle.md § Trade): "TRADER archetype NPCs
literally trade at stations — buy commodities at one station, transit
to another, sell at higher prices. The market-pricing service treats
their transactions identically to player transactions (they affect
supply/price). This means traders can be intercepted, robbed, or
out-bid by players — they're full economic actors."

This module provides three surfaces:

  - ``generate_trade_route`` — at spawn, pick 2–4 stations in the
    trader's home region with complementary commodity surplus/deficit
    within a hop budget. Canon prescribes "travel route between 2–4
    stations on a schedule"; the pairing heuristic (surplus seller →
    deficit buyer) is the minimal mechanical reading.
  - ``build_trader_schedule`` — encode the route as multi-day schedule
    blocks (transit day + trading day per stop, sleep-on-ship blocks
    during hauls, per SYSTEMS/npc-scheduler.md's multi-day TRADER
    pattern). The ``route_cycle`` key extends the daily_schedule JSONB:
    the scheduler resolves blocks from days[canonical_day % cycle_days].
  - ``run_trade_stop`` — executed by Loop A while the trader is in a
    work_station block at the stop's sector: SELL deliverable cargo the
    station buys, then BUY this stop's outbound goods for the next leg.
    Mirrors the player trade path exactly: station row locked FIRST
    (global lock order Player → Station → Ship → NPC → Sector), stock
    dual-written to Station.commodities JSONB AND the MarketPrice row,
    MarketTransaction rows recorded (npc_id set, player_id NULL), and
    a TradingService reprice after the stop.

ADR-0062 E-V4 demand split: NPC trades feed ONLY the per-commodity
``npc_restock_demand`` JSONB key — never ``player_demand_score`` (the
player-facing signal fed by routes/trading.py). Schema home for the
split (per-commodity JSONB keys here vs MarketPrice columns in
DATA_MODELS/economy.md) is a flagged conflict, not silently resolved.

Canon-silent choices (flagged for DECISIONS.md, not invented as canon):
trader wallet seed amount, route hop budget, demand-bump formula,
NPC trades exempt from station tax (tax is an owner lever aimed at
players; canon silent on NPC liability).
"""

import logging
import random
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.market_transaction import MarketPrice, MarketTransaction, TransactionType
from src.models.npc_character import NPCCharacter
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.station import Station
from src.services.trading_service import TradingService

logger = logging.getLogger(__name__)

# Canon-silent tunables (flagged).
ROUTE_MIN_STOPS = 2          # canon: "between 2–4 stations"
ROUTE_MAX_STOPS = 4
ROUTE_HOP_BUDGET = 8         # max hops between consecutive stops
SURPLUS_RATIO = 0.5          # sells + stock above this = surplus seller
DEFICIT_RATIO = 0.5          # buys + stock below this = deficit buyer
DEMAND_SCORE_MIN = 0.0
DEMAND_SCORE_MAX = 2.0


# ---------------------------------------------------------------------------
# Route generation
# ---------------------------------------------------------------------------

def _station_profile(station: Station) -> Dict[str, List[str]]:
    """Commodities this station can supply (sells, surplus stock) and
    wants (buys, deficit stock)."""
    supplies: List[str] = []
    wants: List[str] = []
    for name, cfg in (station.commodities or {}).items():
        capacity = cfg.get("capacity", 0) or 0
        if capacity <= 0:
            continue
        ratio = (cfg.get("quantity", 0) or 0) / capacity
        if cfg.get("sells") and ratio >= SURPLUS_RATIO:
            supplies.append(name)
        if cfg.get("buys") and ratio <= DEFICIT_RATIO:
            wants.append(name)
    return {"supplies": supplies, "wants": wants}


def generate_trade_route(
    db: Session,
    region_id,
    home_sector_id: int,
) -> Optional[List[Dict[str, Any]]]:
    """Pick 2–4 complementary stations for a new trader.

    Each consecutive pair (A → B) must be complementary: A supplies at
    least one commodity B wants, and each leg stays within
    ROUTE_HOP_BUDGET warp hops. Returns a list of stop dicts
    ``{station_id, sector_id, buy_here: [...]}`` where ``buy_here`` is
    what the trader loads at this stop for delivery to the NEXT stop —
    or None when no complementary pair exists in the region.
    """
    from src.services.npc_engagement_service import _hop_distances

    region_sector_ids = {
        row[0]
        for row in db.query(Sector.sector_id)
        .filter(Sector.region_id == region_id)
        .all()
    }
    if not region_sector_ids:
        return None

    stations = [
        s for s in db.query(Station).all()
        if s.sector_id in region_sector_ids and (s.commodities or {})
    ]
    if len(stations) < ROUTE_MIN_STOPS:
        return None

    profiles = {s.id: _station_profile(s) for s in stations}

    # Start from a RANDOM supplying station reachable from the trader's home
    # sector (within the hop budget). Randomizing the start — rather than always
    # taking the nearest supplier — gives each captain a distinct route, so a
    # roster of merchants spreads across the region's lanes instead of all
    # walking one path.
    home_distances = _hop_distances(db, home_sector_id, ROUTE_HOP_BUDGET)
    candidates = [
        s for s in stations
        if profiles[s.id]["supplies"]
        and home_distances.get(s.sector_id) is not None
    ]
    if not candidates:
        # Fall back to any supplier (home graph may be sparse) so marginal
        # regions can still seed a route rather than going trader-less.
        candidates = [s for s in stations if profiles[s.id]["supplies"]]
    if not candidates:
        return None

    route: List[Dict[str, Any]] = []
    current = random.choice(candidates)
    visited = {current.id}

    while len(route) < ROUTE_MAX_STOPS:
        supplies = profiles[current.id]["supplies"]
        distances = _hop_distances(db, current.sector_id, ROUTE_HOP_BUDGET)
        # Gather ALL complementary stations within budget, then pick one at
        # random (weighting nothing) for route variety — not just the nearest.
        options: List[tuple] = []
        for nxt in stations:
            if nxt.id in visited:
                continue
            hops = distances.get(nxt.sector_id)
            if hops is None or hops == 0:
                continue
            goods = [c for c in supplies if c in profiles[nxt.id]["wants"]]
            if goods:
                options.append((nxt, goods))
        if not options:
            break
        best, best_goods = random.choice(options)
        route.append({
            "station_id": str(current.id),
            "sector_id": current.sector_id,
            "buy_here": best_goods,
        })
        visited.add(best.id)
        current = best

    if not route:
        return None
    # Final stop: deliver only (nothing to load — the cycle restarts at
    # the first stop, where the next loop's goods are bought).
    route.append({
        "station_id": str(current.id),
        "sector_id": current.sector_id,
        "buy_here": [],
    })
    if len(route) < ROUTE_MIN_STOPS:
        return None
    return route[:ROUTE_MAX_STOPS]


def build_trader_schedule(route: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Multi-day schedule for a trade route: per stop, one transit day
    (sleep-on-ship, then commute) and one trading day (work_station at
    the stop, then socialize). Cycle length = 2 × stops, resolved by
    the scheduler as days[canonical_day % cycle_days]."""
    days: Dict[str, List[Dict[str, Any]]] = {}
    for i, stop in enumerate(route):
        # Transit day toward stop i: rest 00:00–06:00 on ship, fly the rest.
        days[str(2 * i)] = [
            {"start_minute": 0, "end_minute": 360, "activity": "sleep",
             "location_type": "ship", "location_ref": None},
            {"start_minute": 360, "end_minute": 1440, "activity": "commute",
             "location_type": "station_target",
             "location_ref": {"station_id": stop["station_id"],
                              "sector_id": stop["sector_id"]}},
        ]
        # Trading day at stop i.
        days[str(2 * i + 1)] = [
            {"start_minute": 0, "end_minute": 360, "activity": "sleep",
             "location_type": "ship", "location_ref": None},
            {"start_minute": 360, "end_minute": 1140, "activity": "work_station",
             "location_type": "station",
             "location_ref": {"station_id": stop["station_id"],
                              "sector_id": stop["sector_id"],
                              "stop_index": i,
                              # Thread the route's buy plan into the stop so
                              # run_trade_stop's BUY loop (reads
                              # stop["buy_here"]) actually loads outbound goods.
                              # Without this the trader commutes but never trades.
                              "buy_here": stop.get("buy_here") or []}},
            {"start_minute": 1140, "end_minute": 1440, "activity": "socialize",
             "location_type": "station",
             "location_ref": {"station_id": stop["station_id"],
                              "sector_id": stop["sector_id"]}},
        ]
    return {
        "route_cycle": {"cycle_days": 2 * len(route), "days": days},
        "trade_route": route,
    }


# ---------------------------------------------------------------------------
# Demand split (ADR-0062 E-V4) — NPC side
# ---------------------------------------------------------------------------

def _bump_npc_restock_demand(
    station: Station, commodity_name: str, delta: float
) -> None:
    """Adjust the per-commodity npc_restock_demand key (never the
    player signal). Caller holds the station lock and flags the JSONB."""
    cfg = (station.commodities or {}).get(commodity_name)
    if cfg is None:
        return
    score = cfg.get("npc_restock_demand", 1.0)
    cfg["npc_restock_demand"] = round(
        min(DEMAND_SCORE_MAX, max(DEMAND_SCORE_MIN, score + delta)), 4
    )


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

def _record_transaction(
    db: Session,
    npc: NPCCharacter,
    station: Station,
    transaction_type: TransactionType,
    commodity: str,
    quantity: int,
    unit_price: int,
    market_price: MarketPrice,
) -> None:
    db.add(MarketTransaction(
        player_id=None,
        npc_id=npc.id,
        station_id=station.id,
        transaction_type=transaction_type,
        commodity=commodity,
        quantity=quantity,
        unit_price=unit_price,
        total_value=unit_price * quantity,
        station_buy_price=market_price.buy_price,
        station_sell_price=market_price.sell_price,
        station_quantity=market_price.quantity,
        timestamp=datetime.now(UTC),
    ))


def run_trade_stop(
    db: Session,
    npc: NPCCharacter,
    stop: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """One trading-day visit: sell deliverable cargo, buy outbound
    goods. Idempotent across repeated Loop A passes within the same
    block — after the first pass the cargo/wallet state leaves nothing
    further to do. Returns realtime events (currently none; market
    state is polled)."""
    station = (
        db.query(Station)
        .filter(Station.id == uuid.UUID(stop["station_id"]))
        .populate_existing()
        .with_for_update()
        .first()
    )
    if station is None:
        return []
    if npc.current_sector_id != station.sector_id:
        return []

    ship = None
    if npc.ship_id is not None:
        ship = (
            db.query(Ship)
            .filter(Ship.id == npc.ship_id)
            .with_for_update()
            .first()
        )
    if ship is None or ship.is_destroyed:
        return []

    trading = TradingService(db)
    commodities = station.commodities or {}
    cargo = ship.cargo or {"capacity": 0, "used": 0, "contents": {}}
    contents: Dict[str, int] = dict(cargo.get("contents") or {})
    traded = False

    # --- SELL: everything in the hold this station buys. ---
    for commodity_name in list(contents.keys()):
        held = int(contents.get(commodity_name, 0) or 0)
        if held <= 0:
            continue
        cfg = commodities.get(commodity_name)
        if cfg is None or not cfg.get("buys"):
            continue
        market_price = (
            db.query(MarketPrice)
            .filter(
                MarketPrice.station_id == station.id,
                MarketPrice.commodity == commodity_name,
            )
            .first()
        )
        if market_price is None:
            continue
        unit_price = max(1, int(market_price.buy_price))

        npc.credits = (npc.credits or 0) + unit_price * held
        contents.pop(commodity_name, None)
        cargo["used"] = max(0, int(cargo.get("used", 0)) - held)

        # Dual-write: MarketPrice row AND the commodities JSONB the
        # pricing engine rebuilds from (player path's exact pattern).
        capacity = cfg.get("capacity", 0) or 0
        cfg["quantity"] = (
            min(capacity, cfg.get("quantity", 0) + held)
            if capacity > 0 else cfg.get("quantity", 0) + held
        )
        market_price.quantity = cfg["quantity"]
        market_price.last_transaction_at = datetime.now(UTC)

        # NPC supply arriving REDUCES the station's restock pressure.
        if capacity > 0:
            _bump_npc_restock_demand(station, commodity_name, -held / capacity)
        _record_transaction(
            db, npc, station, TransactionType.SELL,
            commodity_name, held, unit_price, market_price,
        )
        traded = True

    # --- BUY: this stop's outbound goods for the next leg. ---
    for commodity_name in stop.get("buy_here") or []:
        cfg = commodities.get(commodity_name)
        if cfg is None or not cfg.get("sells"):
            continue
        market_price = (
            db.query(MarketPrice)
            .filter(
                MarketPrice.station_id == station.id,
                MarketPrice.commodity == commodity_name,
            )
            .first()
        )
        if market_price is None or market_price.quantity <= 0:
            continue
        unit_price = max(1, int(market_price.sell_price))

        free_space = max(
            0, int(cargo.get("capacity", 0)) - int(cargo.get("used", 0))
        )
        affordable = (npc.credits or 0) // unit_price
        quantity = min(free_space, affordable, int(market_price.quantity))
        if quantity <= 0:
            continue

        npc.credits = (npc.credits or 0) - unit_price * quantity
        contents[commodity_name] = contents.get(commodity_name, 0) + quantity
        cargo["used"] = int(cargo.get("used", 0)) + quantity

        cfg["quantity"] = max(0, cfg.get("quantity", 0) - quantity)
        market_price.quantity = cfg["quantity"]
        market_price.last_transaction_at = datetime.now(UTC)

        # NPC purchases RAISE restock pressure at the source station.
        capacity = cfg.get("capacity", 0) or 0
        if capacity > 0:
            _bump_npc_restock_demand(station, commodity_name, quantity / capacity)
        _record_transaction(
            db, npc, station, TransactionType.BUY,
            commodity_name, quantity, unit_price, market_price,
        )
        traded = True

    if not traded:
        return []

    cargo["contents"] = contents
    ship.cargo = cargo
    flag_modified(ship, "cargo")
    flag_modified(station, "commodities")
    npc.last_seen_at = datetime.now(UTC)
    db.flush()

    # Reprice from the post-trade stock (station row already locked in
    # this transaction — the reprice's own lock is a no-wait re-grab).
    trading.update_market_prices(station.id)

    logger.info(
        "Trader %s completed a trade stop at %s (credits now %s)",
        npc.display_name, station.name, npc.credits,
    )
    return []
