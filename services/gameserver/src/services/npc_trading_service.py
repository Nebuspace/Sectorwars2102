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
    a TradingService reprice after the stop. Market participation
    (npc-traders.md § Market participation): each leg carries the SAME
    region tariff (trading_service.compute_region_tariff_multiplier) and
    the SAME station owner tax (realized 40/30/30 via
    port_ownership_service.realize_port_revenue) a player trade pays, and
    accrues port-takeover hostility into a SEPARATE, CAPPED NPC sub-ledger
    on Station.ownership — kept apart from the player-attributed takeover
    path so an NPC route alone can never trigger a takeover cascade.

ADR-0062 E-V4 demand split: NPC trades feed ONLY the per-commodity
``npc_restock_demand`` JSONB key — never ``player_demand_score`` (the
player-facing signal fed by routes/trading.py). Schema home for the
split (per-commodity JSONB keys here vs MarketPrice columns in
DATA_MODELS/economy.md) is a flagged conflict, not silently resolved.

Canon-silent choices (flagged for DECISIONS.md, not invented as canon):
trader wallet seed amount, route hop budget, demand-bump formula, the
NPC-driven port-takeover hostility per-trade weight + cap. NPC station-tax
liability is NO LONGER canon-silent: npc-traders.md § Market participation
now states a trader trade pays the same tariffs and hostility metrics as a
player, so NPC trades pay the station owner tax exactly like players (an
unowned station still levies none — the tax is an owner lever).
"""

import logging
import math
import random
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.docking import DockingSlipOccupancy
from src.models.market_transaction import MarketPrice, MarketTransaction, TransactionType
from src.models.npc_character import NPCCharacter, NPCLifecycleStage
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.station import Station, StationType
from src.services import docking_service
from src.services.scheduler._common import (
    canonical_day_number,
    canonical_minute_of_day,
    canonical_weekday,
    resolve_schedule_block,
)
from src.services.trading_service import (
    TradingService,
    compute_region_tariff_multiplier,
)

logger = logging.getLogger(__name__)

# Canon-silent tunables (flagged).
ROUTE_MIN_STOPS = 2          # canon: "between 2–4 stations"
ROUTE_MAX_STOPS = 4
ROUTE_HOP_BUDGET = 8         # max hops between consecutive stops
SURPLUS_RATIO = 0.5          # sells + stock above this = surplus seller
DEFICIT_RATIO = 0.5          # buys + stock below this = deficit buyer
DEMAND_SCORE_MIN = 0.0
DEMAND_SCORE_MAX = 2.0

# --- Notoriety drift (npc-traders.md § Notoriety) --------------------------
# Canon: "Notoriety drifts dynamically: a trader caught smuggling rises toward
# notorious, while honest trade decays it back toward reputable." The 0–100
# scruples axis, the ≥ 50 lawful-target threshold, and the −100 attack_innocent
# penalty are already shipped; this is the dynamic-drift step the doc lists as
# 📐 Design-only. Illicit trade is read from in-data signal — a trade stop at a
# BLACK_MARKET-type station (black-market.md: smugglers move illegal goods at
# black-market venues). Any other station type is honest trade.
#
# ⚠️ NO-CANON MAGNITUDES — FLAG FOR MAX / DECISIONS. npc-traders.md prescribes
# the DIRECTION of drift but gives NO per-trade magnitude OR cadence. Drift is
# gated to AT MOST ONCE per work_station block (see _drift_notoriety): a single
# trading-day visit to a both-buy+sell black-market station is re-driven by Loop
# A on every pass through that block (~13h of canonical time), so an ungated
# drift would over-count one captain's smuggling. Both deltas are deliberately
# small so a captain's standing shifts over many legs rather than flipping on a
# single block: a trader needs ~10 illicit blocks to cross a 24→50 band boundary,
# and honest trade erodes notoriety roughly half as fast (a reputation is easier
# to lose than to rebuild). Tune once Max sets canon.
NOTORIETY_AXIS_MIN = 0
NOTORIETY_AXIS_MAX = 100
NOTORIETY_ILLICIT_DRIFT = 3   # NO-CANON: per illicit (black-market) work block, toward 100
NOTORIETY_HONEST_DECAY = 1    # NO-CANON: per honest work block, toward 0

# Sentinel key in NPCCharacter.daily_schedule (JSONB) recording the last
# work_station block this trader drifted notoriety on. A block is identified by
# (canonical_day_number, station_id): the same station appears at most once per
# canonical day in a trader's route cycle, so a new day is a new block and drift
# is permitted again. Stored here (not a new column) so no migration is needed.
NOTORIETY_DRIFT_SENTINEL_KEY = "notoriety_drift_block"

# --- Port-takeover hostility: NPC-driven contribution (npc-traders.md §
#     Market participation) -------------------------------------------------
# Canon: "Port-takeover hostility metrics accumulate from trader trades.
# Anti-exploit guard: hostility accounting distinguishes NPC- from player-driven
# contribution, so an NPC route can never trigger a takeover cascade a player
# never caused."
#
# STRUCTURAL ISOLATION (already true, reinforced here): the economic-takeover
# engine (port_ownership_service._month_stats / monthly_volume) measures a
# challenger's share as ``MarketTransaction.player_id == challenger_id`` volume
# over the station's BUY+SELL total. NPC trades are written with
# ``player_id=None`` (npc_id set, see _record_transaction), so they can NEVER be
# attributed to a player challenger — an NPC route therefore cannot satisfy the
# >50%-of-volume takeover threshold for anyone. NPC volume only sits in the
# DENOMINATOR (the station total), where it DILUTES a player's share rather than
# advancing it. So no code change can make an NPC route trigger a cascade; this
# block keeps the NPC-driven hostility metric in a SEPARATE, CAPPED station
# sub-ledger that the takeover engine never reads, so the two contributions are
# distinguishable and the NPC one is bounded.
#
# Storage: a namespaced key inside the existing Station.ownership JSONB —
# ``npc_takeover_hostility`` — distinct from every key port_ownership_service
# uses (defense_fund / operating_fund / insolvency_months / acquisition_cost /
# player_id / acquired_at …), so the two ledgers never collide and no migration
# is needed. The takeover engine reads MarketTransaction + monthly_history, never
# this key, so this metric is purely an isolated, auditable NPC-side tally.
NPC_HOSTILITY_LEDGER_KEY = "npc_takeover_hostility"

# ⚠️ NO-CANON: the per-trade hostility weight and the cap are not specified by
# npc-traders.md (canon gives the DIRECTION — "accumulate from trader trades" —
# and the isolation REQUIREMENT, but no magnitude). A conservative, gross-value-
# proportional weight with a hard cap keeps the NPC tally small and bounded so it
# can never be mistaken for, or grow into, a player-scale takeover signal. Tune
# once Max sets canon.
NPC_HOSTILITY_PER_TRADE_WEIGHT = 0.001   # NO-CANON: hostility units per credit of NPC trade value
NPC_HOSTILITY_CAP = 1000.0               # NO-CANON: hard ceiling on accumulated NPC-driven hostility per station

# --- Docking-slip anti-camp tenure (npc-traders.md § Market participation,
#     WO-P9-realtime-npc-trader-slips) ---------------------------------------
# ⚠️ NO-CANON MAGNITUDE -- FLAG FOR MAX / DECISIONS. Canon states the
# REQUIREMENT ("a slip-tenure limit forces traders to release slips promptly
# so they cannot be used to grief player docking") but gives no number.
# Picked conservatively: well under docking_service.BUMP_MIN_TENURE_HOURS
# (4 canonical hours, the player-side "you may now pay to evict a squatter"
# threshold) so a camping trader is never the galaxy's longest-tenured
# occupant, and well under the ~13-canonical-hour work_station window a
# trading-day schedule block reserves (build_trader_schedule) -- otherwise
# the ceiling would never actually bind and release_stale_trader_slips'
# schedule-block-membership check (the normal, prompt release path) would
# be the ONLY real release trigger, leaving the ceiling a dead safety net.
TRADER_SLIP_TENURE_CEILING_HOURS = 2.0   # NO-CANON: conservative anti-camp bound


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
# Notoriety drift (npc-traders.md § Notoriety)
# ---------------------------------------------------------------------------

def _work_station_block_ref(station: Station) -> str:
    """Stable identifier for the trader's CURRENT work_station block:
    ``"<canonical_day_number>:<station_id>"``. A trader's route cycle places
    a given station in at most one work_station block per canonical day, so
    this string is unique to one trading-day visit — a fresh day yields a new
    ref and re-permits drift. Imported lazily to avoid a module import cycle
    (npc_scheduler_service imports npc_trading_service)."""
    from src.services.npc_scheduler_service import canonical_day_number

    return f"{canonical_day_number()}:{station.id}"


def _drift_notoriety(npc: NPCCharacter, station: Station) -> bool:
    """Drift a trader's notoriety on a completed work_station block: illicit
    (black-market) trade rises toward NOTORIOUS, honest trade decays toward
    REPUTABLE. Bounded to the 0–100 axis.

    Gated to AT MOST ONCE per work_station block. Loop A re-drives the same
    trading-day visit on every pass through the block (a both-buy+sell
    black-market station re-trades on repeat passes), so without a gate one
    captain's smuggling would drift more than once per block. The last block
    that drifted is stamped in ``npc.daily_schedule[NOTORIETY_DRIFT_SENTINEL_KEY]``;
    a repeat pass for the same block is a no-op. Returns True iff it drifted
    (so the caller flags the daily_schedule JSONB as modified).

    The caller holds the NPC row in the same transaction (it is a live,
    in-session NPCCharacter); no separate lock is taken — notoriety is the
    NPC's own scruples axis, never read mid-flight by another locked actor in
    this path, and the sentinel lives on the same NPC row."""
    block_ref = _work_station_block_ref(station)
    schedule = npc.daily_schedule
    if not isinstance(schedule, dict):
        schedule = {}
        npc.daily_schedule = schedule
    if schedule.get(NOTORIETY_DRIFT_SENTINEL_KEY) == block_ref:
        # This block already drifted notoriety on an earlier Loop-A pass.
        return False

    current = npc.notoriety or 0
    illicit = getattr(station, "type", None) == StationType.BLACK_MARKET
    delta = NOTORIETY_ILLICIT_DRIFT if illicit else -NOTORIETY_HONEST_DECAY
    npc.notoriety = max(
        NOTORIETY_AXIS_MIN, min(NOTORIETY_AXIS_MAX, current + delta)
    )
    schedule[NOTORIETY_DRIFT_SENTINEL_KEY] = block_ref
    return True


# ---------------------------------------------------------------------------
# Market participation: region tariff + station tax + NPC-driven hostility
# (npc-traders.md § Market participation — "A trader trade is treated
#  identically to a player transaction wherever it touches the shared market.")
# ---------------------------------------------------------------------------

def _station_tax_rate(station: Station) -> float:
    """Station owner trade tax, applied EXACTLY like the player path
    (routes/trading.py): the tax is an OWNER lever, so an unowned (NPC-run)
    station levies none. Defensive: a None/absent rate degrades to 0.0."""
    if station.owner_id is None:
        return 0.0
    rate = getattr(station, "tax_rate", None)
    return float(rate) if rate is not None else 0.0


def _realize_station_tax(db: Session, station: Station, tax_amount: int) -> None:
    """Route a withheld trade tax to the station per the canon 40/30/30 split
    (port_ownership_service.realize_port_revenue) — the SAME realization the
    player buy/sell path uses. The station row is already locked in this
    transaction; realize_port_revenue re-grabs it (a no-wait re-grab of a row
    this txn already holds). Imported lazily and DEFENSIVE: any error falls back
    to the whole-tax-to-treasury behaviour the player path also falls back to,
    so a revenue-split hiccup can never break an NPC trade."""
    if tax_amount <= 0:
        return
    try:
        from src.services import port_ownership_service
        port_ownership_service.realize_port_revenue(db, station, tax_amount)
    except Exception:
        logger.warning(
            "realize_port_revenue failed (npc trade); falling back to treasury",
            exc_info=True,
        )
        station.treasury_balance = (station.treasury_balance or 0) + tax_amount


def _accrue_npc_hostility(station: Station, trade_value: int) -> None:
    """Accumulate NPC-DRIVEN port-takeover hostility in a separate, capped
    sub-ledger on Station.ownership (npc-traders.md § Market participation).

    Kept entirely apart from the player-attributed takeover path (which the
    economic engine derives from MarketTransaction.player_id volume): this tally
    lives under NPC_HOSTILITY_LEDGER_KEY, is proportional to the NPC trade's
    gross value, and is hard-capped — so the NPC contribution is distinguishable
    from the player one and can never grow into a player-scale takeover signal.
    Caller holds the station lock and MUST flag_modified(station, 'ownership')."""
    if trade_value <= 0:
        return
    ledger = station.ownership
    if not isinstance(ledger, dict):
        ledger = {}
        station.ownership = ledger
    current = float(ledger.get(NPC_HOSTILITY_LEDGER_KEY, 0.0) or 0.0)
    accrued = current + trade_value * NPC_HOSTILITY_PER_TRADE_WEIGHT
    ledger[NPC_HOSTILITY_LEDGER_KEY] = round(min(NPC_HOSTILITY_CAP, accrued), 4)


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

    # WO-P9-realtime-npc-trader-slips (npc-traders.md § Market participation):
    # claim a real transient docking slip through the SAME occupancy surface
    # the player dock path uses, before the trade program runs. Best-effort
    # and non-gating (see docking_service.acquire_for_npc's own docstring) --
    # a full station never blocks the trade, it just means this trader trades
    # without holding a slip this stop. Idempotent: a no-op on every repeat
    # Loop A pass through the same work_station block once the first pass has
    # already claimed one. Release is NOT handled here -- run_trade_stop has
    # no signal for when the NPC's block ENDS (it simply stops being called);
    # release_stale_trader_slips (called once per Loop A tick, see
    # npc_tick_loops.py) is the release side, gated on block-membership +
    # an anti-camp tenure ceiling so a slip is never stranded even if this
    # trader's schedule stalls.
    docking_service.acquire_for_npc(db, station, npc, ship_id=ship.id)

    trading = TradingService(db)
    commodities = station.commodities or {}
    cargo = ship.cargo or {"capacity": 0, "used": 0, "contents": {}}
    contents: Dict[str, int] = dict(cargo.get("contents") or {})
    traded = False

    # Market participation (npc-traders.md § Market participation): a trader
    # trade is treated identically to a player transaction wherever it touches
    # the shared market. Reuse the SAME region-tariff helper the player buy/sell
    # path uses (routes/trading.py via trading_service.compute_region_tariff_
    # multiplier) so an NPC trade carries the region tariff like a player trade.
    # Read once per stop (the rate is per-region, not per-commodity); defensive —
    # the helper degrades to a neutral (1.0, 0.0) on any lookup failure.
    tariff_mult, _ = compute_region_tariff_multiplier(db, station)
    tax_rate = _station_tax_rate(station)
    # Tax is ACCUMULATED across all legs and realized ONCE at the end (after the
    # main flush). realize_port_revenue re-locks the station with
    # populate_existing(), which would discard this method's un-flushed
    # commodities / ownership mutations if called mid-loop — so it must run only
    # after those changes are persisted (see the realization site below).
    total_tax = 0
    npc_hostility_value = 0  # gross NPC trade value this stop (for the isolated ledger)

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
        # SELL: the trader is PAID the station's buy_price. The region tariff
        # is a surcharge on the shared market, so it REDUCES the seller's payout
        # — divide, exactly as the player sell path does
        # (routes/trading.py: SELL = base_buy_price ÷ tariff). Floor at 1/unit.
        base_unit_price = max(1, int(market_price.buy_price))
        unit_price = (
            max(1, int(base_unit_price / tariff_mult))
            if tariff_mult > 0 else base_unit_price
        )

        gross_payout = unit_price * held
        # Station tax (owner lever): withheld from the trader's proceeds and
        # realized to the station's treasury per the 40/30/30 split — the SAME
        # treatment a player sell receives (unowned stations levy none). The tax
        # is accumulated and realized once after the main flush (see total_tax).
        tax_amount = int(gross_payout * tax_rate)
        net_payout = gross_payout - tax_amount

        npc.credits = (npc.credits or 0) + net_payout
        total_tax += tax_amount
        npc_hostility_value += gross_payout
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
        # BUY: the trader PAYS the station's sell_price. The region tariff is a
        # surcharge that RAISES what the buyer pays — multiply, exactly as the
        # player buy path does (routes/trading.py: BUY = base_sell_price ×
        # tariff). Floor at 1/unit.
        base_unit_price = max(1, int(market_price.sell_price))
        unit_price = (
            max(1, int(base_unit_price * tariff_mult))
            if tariff_mult > 0 else base_unit_price
        )
        # Station tax (owner lever) is charged ON TOP of the goods cost — the
        # SAME as a player buy (total_with_tax = total_cost + tax). The per-unit
        # all-in cost gates affordability so the trader never overspends its
        # wallet. Round the per-unit tax UP (ceil) so the bound is a CONSERVATIVE
        # upper estimate: per-unit flooring could otherwise let the once-floored
        # aggregate tax (int(total_cost*tax_rate)) exceed quantity*all_in_per_unit
        # and edge the wallet negative. all_in_per_unit is used ONLY for the
        # affordability bound, not the charged price.
        per_unit_tax_ceil = math.ceil(unit_price * tax_rate) if tax_rate > 0 else 0
        all_in_per_unit = max(1, unit_price + per_unit_tax_ceil)

        free_space = max(
            0, int(cargo.get("capacity", 0)) - int(cargo.get("used", 0))
        )
        affordable = (npc.credits or 0) // all_in_per_unit
        quantity = min(free_space, affordable, int(market_price.quantity))
        if quantity <= 0:
            continue

        total_cost = unit_price * quantity
        tax_amount = int(total_cost * tax_rate)
        total_with_tax = total_cost + tax_amount
        # Final guard: never let rounding push the wallet negative (trim 1 unit
        # if the aggregate all-in cost edged past the wallet). Defensive belt-and-
        # suspenders on top of the conservative per-unit bound above.
        while quantity > 0 and total_with_tax > (npc.credits or 0):
            quantity -= 1
            total_cost = unit_price * quantity
            tax_amount = int(total_cost * tax_rate)
            total_with_tax = total_cost + tax_amount
        if quantity <= 0:
            continue

        npc.credits = (npc.credits or 0) - total_with_tax
        total_tax += tax_amount
        npc_hostility_value += total_cost
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

    # Notoriety drift on a completed work_station block (npc-traders.md §
    # Notoriety): smuggling at a black-market venue raises notoriety toward
    # NOTORIOUS; honest trade decays it back toward REPUTABLE. Gated to AT MOST
    # ONCE per work block — Loop A re-drives this trading-day visit on every
    # pass, so a both-buy+sell black-market station would otherwise drift twice.
    # Folded into this method's single flush — same transaction, no extra lock.
    drifted = _drift_notoriety(npc, station)

    # Port-takeover hostility from this trader trade (npc-traders.md § Market
    # participation): accumulate the NPC-DRIVEN contribution in its OWN capped
    # station sub-ledger, kept entirely apart from the player-attributed takeover
    # path (which the engine derives from MarketTransaction.player_id volume).
    # NPC trades write player_id=None, so they can never count toward a player's
    # >50% takeover share — this isolated tally makes the NPC contribution
    # distinguishable and bounded, so an NPC route alone can never feed a
    # takeover cascade. Folded into this method's single flush — same txn, no
    # extra lock (the station row is already held above).
    if npc_hostility_value > 0:
        _accrue_npc_hostility(station, npc_hostility_value)

    cargo["contents"] = contents
    ship.cargo = cargo
    flag_modified(ship, "cargo")
    flag_modified(station, "commodities")
    # station.ownership now carries the NPC hostility sub-ledger; flag it so the
    # write lands on this flush BEFORE the post-flush tax realization re-reads
    # ownership via populate_existing (otherwise the hostility key would be lost
    # when realize_port_revenue reloads the row).
    flag_modified(station, "ownership")
    if drifted:
        # The drift stamped the block sentinel into the daily_schedule JSONB;
        # flag it so the change is persisted on this flush.
        flag_modified(npc, "daily_schedule")
    npc.last_seen_at = datetime.now(UTC)
    db.flush()

    # Realize the accumulated station tax ONCE, AFTER the main flush. The 40/30/30
    # split (realize_port_revenue) re-locks the station with populate_existing(),
    # which reloads attributes from the DB — so it runs only now that this
    # method's commodities / ownership / hostility mutations are persisted (a
    # mid-loop call would have discarded un-flushed JSONB edits). Defensive: any
    # error falls back to the whole-tax-to-treasury behaviour the player path
    # also uses, so a split hiccup can never break the trade.
    if total_tax > 0:
        _realize_station_tax(db, station, total_tax)
        db.flush()

    # Reprice from the post-trade stock (station row already locked in
    # this transaction — the reprice's own lock is a no-wait re-grab).
    trading.update_market_prices(station.id)

    logger.info(
        "Trader %s completed a trade stop at %s (credits now %s)",
        npc.display_name, station.name, npc.credits,
    )
    return []


def release_stale_trader_slips(db: Session) -> int:
    """Release every TRADER-held docking slip that should no longer be
    held (WO-P9-realtime-npc-trader-slips). Called once per Loop A tick
    (npc_tick_loops.run_loop_a), mirroring npc_movement_service.
    ensure_capital_fed_presence's own per-tick reconciliation-sweep shape
    -- idempotent, self-healing, safe to call every tick.

    run_trade_stop has no signal for when its own work_station block ENDS
    (it simply stops being called once the schedule rolls past it — the
    scheduler's elif-dispatch in npc_tick_loops.run_loop_a has no driver
    for the following `socialize` block at the same station), so release
    can't live there. Two independent triggers, either one releases:

    1. Block-membership check (the normal, prompt release): the owning
       NPC's CURRENTLY resolved schedule block is no longer a
       WORK_STATION/station block at THIS SAME station. Covers the
       common case — trading finished, schedule moved on to socialize/
       commute/a new day — releasing promptly rather than waiting out
       the full tenure ceiling on every single stop.
    2. Tenure ceiling (the safety net, team-caution "must still release
       even if its schedule stalls"): TRADER_SLIP_TENURE_CEILING_HOURS
       exceeded, regardless of what the schedule currently says. Also
       covers the NPC row vanishing or reaching KIA/RETIRED — a stale
       occupancy with no live schedule to consult must still age out.

    Does NOT commit — the caller's flush/commit boundary applies (mirrors
    every other Loop A driver in this scheduler)."""
    occupancies = (
        db.query(DockingSlipOccupancy)
        .filter(DockingSlipOccupancy.npc_id.isnot(None))
        .all()
    )
    if not occupancies:
        return 0

    now = datetime.now(UTC)
    minute = canonical_minute_of_day(now)
    weekday = canonical_weekday(now)
    day_number = canonical_day_number(now)

    released = 0
    for occ in occupancies:
        if docking_service.occupant_tenure_hours(occ, now) >= TRADER_SLIP_TENURE_CEILING_HOURS:
            db.delete(occ)
            released += 1
            continue

        npc = db.query(NPCCharacter).filter(NPCCharacter.id == occ.npc_id).first()
        if npc is None or npc.lifecycle_stage in (
            NPCLifecycleStage.KIA, NPCLifecycleStage.RETIRED,
        ):
            db.delete(occ)
            released += 1
            continue

        block = resolve_schedule_block(
            npc.daily_schedule or {}, minute, weekday, day_number
        )
        ref = (block or {}).get("location_ref") or {}
        still_working_here = (
            block is not None
            and str(block.get("activity", "")).upper() == "WORK_STATION"
            and str(block.get("location_type", "")) == "station"
            and str(ref.get("station_id")) == str(occ.station_id)
        )
        if not still_working_here:
            db.delete(occ)
            released += 1

    if released:
        db.flush()
    return released
