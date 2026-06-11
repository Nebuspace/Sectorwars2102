"""
Port (station) ownership service: listings, sealed-bid purchase, owner
powers (tax / treasury), and the economic-takeover campaign engine.

Canon reference: FEATURES/economy/port-ownership (sw2102-docs).

LAZY ENGINE: there is no background worker. `resolve_listing()` settles an
expired grace window on first read past expiry; `evaluate_campaign()`
catches up takeover months one by one and settles an expired counter
window. All durations are CANONICAL and pass through src.core.game_time,
so GAME_TIME_SCALE compresses every window uniformly on dev.

DOCUMENTED INTERPRETATIONS (where canon is summarized or silent):
  * Reputation gate — canon requires "'Trusted' tier or better", but
    Player.reputation_tier uses the 8-tier PERSONAL scale (Villain ..
    Legendary; see personal_reputation_service.REPUTATION_TIERS), which has
    no literal 'Trusted'. The faction-scale TRUSTED level sits 3 steps
    above NEUTRAL out of 8 positive steps (~37.5% of the positive band);
    mapped onto the personal scale that is a score of ~375, which lands in
    'Heroic' (250-499). v1 gate: reputation_tier in {'Heroic','Legendary'}.
  * List price — price = clamp(class_base x region_modifier + one scaled
    month of trailing revenue + upgrades, [250k, 2M]) + treasury:
      - class_base: classes 1-3 low (250k-400k), 6-7 mid (450k-550k),
        8-11 high (800k-1.2M), within canon's bands.
      - region_modifier: 1.10 when the station's region declares an
        economic_specialization (a specialized economy raises commercial
        property values), else 1.0.
      - revenue: trailing 90-canonical-day gross MarketTransaction volume
        at the station, divided by 3 (i.e. one canonical month of gross
        trade is priced into the business).
      - upgrades: 10,000 credits per enabled service flag on the station.
      - treasury_balance: added in full ON TOP of the clamp — the treasury
        CONVEYS 1:1 with the station on sale, so folding it inside the
        clamp would let an owner park >2M of treasury in a station and
        sell the package at the 2M ceiling (treasury arbitrage).
  * Offers must be at or above the list price (an "offer" accepts the ask;
    the sealed-bid auction only ever bids the price UP).
  * Sale proceeds for an NPC-owned (unowned) station are a credit sink —
    v1 only lists unowned stations, so there is no seller to pay.
  * A listing whose grace window expires with NO offers is 'cancelled'.
  * Hostile pricing (takeover month test) — the challenger's average SELL
    unit price at the station that month undercuts the prevailing
    station-pays price: avg(MarketTransaction.station_buy_price) across
    the month's snapshotted transactions x 0.97. When NO snapshots exist
    for the month (legacy rows written before the trading routes started
    populating the snapshot columns), the verdict is hostile=True — v1
    treats volume share as the PRIMARY gate and the pricing snapshot as
    corroborative, so missing snapshots must not silently block canon
    takeovers.
  * Bot-farming heuristic (dispute auto-arbitration) — per commodity over
    the qualifying months, matched volume = 2 x min(buy value, sell value);
    if matched volume exceeds 80% of the challenger's total volume the
    campaign fails as bot-farming, else the dispute is rejected.
  * counter_match compares the owner's volume against the challenger's in
    the CURRENT IN-PROGRESS scaled month, both recomputed live from
    MarketTransaction (the completed months were by definition lost to the
    challenger — comparing against a finished month made the match
    unwinnable). Success resets the campaign clock (status 'countered',
    months_satisfied 0) and evaluation continues from the NEXT month.
  * Forced-sale condition_multiplier is 1.0 in v1 (station condition is
    not modeled yet).
  * acquisition_cost reads from station.ownership['acquisition_cost']
    (written on every transfer here); stations owned before this feature
    fall back to acquisition_requirements['base_price'].
  * If the challenger cannot pay the forced-sale price when the transfer
    comes due, the campaign FAILS (the owner keeps the station).

Lock-ordering contract (matches construction_service): the STATION row is
locked first, then PLAYER rows in ASCENDING player id. with_for_update()
does not refresh already-loaded instances — re-reads chain
.populate_existing() (construction_service.advance reference pattern).
No function here commits; the calling route owns the transaction.
"""
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.market_transaction import MarketTransaction, TransactionType
from src.models.player import Player
from src.models.port_ownership import PurchaseOffer, StationListing, TakeoverCampaign
from src.models.station import Station, player_stations

logger = logging.getLogger(__name__)


class PortOwnershipError(Exception):
    """Raised on invalid ownership actions; carries an HTTP status hint."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Canon constants
# ---------------------------------------------------------------------------

# Purchasable station classes (canon excludes ONLY 0/4/5, plus the
# structural exclusions handled in is_listable: population hubs,
# spacedocks, and TradeDocks).
PURCHASABLE_CLASSES = {1, 2, 3, 6, 7, 8, 9, 10, 11}

# Class base prices: 1-3 low (canon ~250k-400k), 6-7 mid (~450k-550k),
# 8-11 high (canon ~600k-1.2M).
CLASS_BASE_PRICE = {
    1: 250_000,   # Mining Operation
    2: 300_000,   # Agricultural Center
    3: 400_000,   # Industrial Hub
    6: 450_000,   # Mixed Market
    7: 550_000,   # Resource Exchange
    8: 800_000,   # Black Hole (Premium Buyer)
    9: 850_000,   # Nova (Premium Seller)
    10: 1_200_000,  # Luxury Market
    11: 1_000_000,  # Advanced Tech Hub
}

PRICE_FLOOR = 250_000
PRICE_CEILING = 2_000_000
REVENUE_WINDOW_DAYS = 90            # trailing canonical days priced into a sale
REGION_SPECIALIZATION_MODIFIER = 1.10
SERVICE_UPGRADE_VALUE = 10_000      # credits per enabled service flag

GRACE_HOURS = 24.0                  # canonical purchase grace window
COUNTER_WINDOW_HOURS = 7 * 24.0     # canonical owner counter window
MONTH_HOURS = 30 * 24.0             # 1 scaled month = 30 canonical days

TAKEOVER_SHARE_THRESHOLD = 0.5      # challenger needs >50% of monthly volume
TAKEOVER_MONTHS_REQUIRED = 3        # consecutive satisfied months
BOT_FARM_FRACTION = 0.8             # >80% self-cancelling volume = bot farming
CONDITION_MULTIPLIER = 1.0          # v1: station condition not modeled
HOSTILE_UNDERCUT_FACTOR = 0.97      # selling >=3% under the station-pays price
CATCHUP_EVAL_LIMIT = 3              # lazy month catch-up: evaluate at most the
                                    # trailing N months individually; older
                                    # months batch-skip as unsatisfied

MIN_TAX_RATE = 0.0
MAX_TAX_RATE = 0.25

# Personal reputation tiers in ascending order
# (personal_reputation_service.REPUTATION_TIERS). See the module docstring
# for the 'Trusted' -> 'Heroic' mapping rationale.
TIER_ORDER = [
    "Villain", "Criminal", "Outlaw", "Suspicious",
    "Neutral", "Lawful", "Heroic", "Legendary",
]
MIN_BUYER_TIER = "Heroic"


# ---------------------------------------------------------------------------
# Pure helpers (no DB) — unit-tested directly
# ---------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def tier_allows_purchase(tier: Optional[str]) -> bool:
    """Canon reputation gate: 'Trusted' or better (mapped to 'Heroic'+)."""
    try:
        return TIER_ORDER.index(tier) >= TIER_ORDER.index(MIN_BUYER_TIER)
    except ValueError:
        return False  # unknown tier string: fail closed


def clamp_price(value: float) -> int:
    """Canon price clamp: [250,000, 2,000,000] credits."""
    return int(max(PRICE_FLOOR, min(PRICE_CEILING, value)))


def business_price_with_treasury(business_raw: float, treasury: int) -> int:
    """List-price composition rule: clamp ONLY the business component
    (class base x region modifier + revenue + upgrades) to [250k, 2M],
    then add the treasury balance ON TOP. The treasury conveys 1:1 with
    the sale; folding it inside the clamp let an owner park >2M of
    treasury in a station and sell the package at the 2M ceiling
    (treasury arbitrage)."""
    return clamp_price(business_raw) + int(treasury or 0)


def hostility_verdict(
    challenger_avg_sell: Optional[float], avg_station_buy: Optional[float]
) -> bool:
    """Hostile-pricing verdict for one takeover month.

    Hostile = the challenger's average SELL unit price undercuts the
    prevailing station-pays price (avg snapshotted station_buy_price) by
    >=3% (HOSTILE_UNDERCUT_FACTOR).

    challenger_avg_sell None (the challenger sold nothing that month) ->
    False: there is no sell-side pricing to be hostile with.

    avg_station_buy None (no station_buy_price snapshots that month —
    legacy MarketTransaction rows written before the trading routes
    populated the snapshot columns) -> True: v1 treats volume share as
    the PRIMARY gate and the pricing snapshot as corroborative only, so
    months without snapshots must not silently block canon takeovers."""
    if challenger_avg_sell is None:
        return False
    if avg_station_buy is None:
        return True
    return challenger_avg_sell < avg_station_buy * HOSTILE_UNDERCUT_FACTOR


def catch_up_plan(
    last_evaluated: int, completed: int, limit: int = CATCHUP_EVAL_LIMIT
) -> Tuple[Optional[Tuple[int, int]], range]:
    """Bound the lazy month catch-up: with more than `limit` months pending,
    batch-skip the older ones in one step and only evaluate the trailing
    `limit` individually. Returns (skipped_inclusive_range | None,
    months_to_evaluate). A dormant campaign re-read after a long absence
    otherwise issues an unbounded number of per-month aggregate queries."""
    pending = completed - last_evaluated
    if pending > limit:
        first_evaluated = completed - limit
        return (last_evaluated, first_evaluated - 1), range(first_evaluated, completed)
    return None, range(last_evaluated, completed)


def current_month_index(started_at: datetime, now: Optional[datetime] = None) -> int:
    """Index of the IN-PROGRESS scaled month (0-based) since `started_at`."""
    return int(game_time.canonical_hours_since(started_at, now) // MONTH_HOURS)


def settlement_owner_guard(owner_id_now, owner_at_eligibility) -> str:
    """Owner-change guard at forced-sale settlement time:
      'failed'  — the station has NO owner anymore (nothing to force-sell);
      'reset'   — the owner changed since eligibility was earned (the new
                  owner gets a fresh campaign run: back to 'building');
      'proceed' — same owner, settle normally."""
    if owner_id_now is None:
        return "failed"
    if owner_at_eligibility is not None and str(owner_id_now) != str(owner_at_eligibility):
        return "reset"
    return "proceed"


def is_listable(station: Any) -> bool:
    """Canon exclusions: classes 0/4/5 are never purchasable (everything
    else — 1-3 / 6-11 — is); never population hubs
    (station-side analogues: quest hubs and faction headquarters — the
    population_hub flag proper lives on planets), spacedocks (is_spacedock
    covers both the flag and the SHIPYARD-with-flag form), or TradeDocks
    (tradedock_tier non-null). Destroyed or explicitly non-ownable stations
    are never listable."""
    station_class = getattr(station, "station_class", None)
    class_value = getattr(station_class, "value", station_class)
    return bool(
        getattr(station, "is_player_ownable", False)
        and not getattr(station, "is_destroyed", False)
        and class_value in PURCHASABLE_CLASSES
        and not getattr(station, "is_spacedock", False)
        and getattr(station, "tradedock_tier", None) is None
        and not getattr(station, "is_quest_hub", False)
        and not getattr(station, "is_faction_headquarters", False)
    )


def pick_winner(offers: Sequence[Any]) -> Tuple[Any, List[Any]]:
    """Sealed-bid resolution: highest bid wins; ties go to the EARLIEST
    offer. Returns (winner, losers). Pure on (bid, created_at) attributes."""
    if not offers:
        raise ValueError("pick_winner requires at least one offer")
    ordered = sorted(
        offers,
        key=lambda o: (-o.bid, _aware(o.created_at) if o.created_at else datetime.max.replace(tzinfo=UTC)),
    )
    return ordered[0], ordered[1:]


def self_cancelling_fraction(
    buy_by_commodity: Dict[str, int], sell_by_commodity: Dict[str, int]
) -> float:
    """Fraction of total volume that is self-cancelling buy/sell pairs:
    per commodity, matched = 2 x min(buy value, sell value). 0.0 when the
    challenger has no volume at all."""
    total = sum(buy_by_commodity.values()) + sum(sell_by_commodity.values())
    if total <= 0:
        return 0.0
    matched = sum(
        2 * min(buy_by_commodity.get(c, 0), sell_by_commodity.get(c, 0))
        for c in set(buy_by_commodity) | set(sell_by_commodity)
    )
    return matched / total


def forced_sale_value(avg_monthly_revenue: float, acquisition_cost: int) -> int:
    """Canon forced-sale price: clamp(avg-monthly-revenue x 12 x
    condition_multiplier, acquisition_cost, 2 x acquisition_cost)."""
    raw = avg_monthly_revenue * 12 * CONDITION_MULTIPLIER
    return int(max(acquisition_cost, min(2 * acquisition_cost, raw)))


def month_satisfied(share: float, hostile: bool) -> bool:
    """A takeover month counts when share > 50% AND pricing was hostile."""
    return share > TAKEOVER_SHARE_THRESHOLD and hostile


def apply_month(campaign: Any, record: Dict[str, Any]) -> None:
    """Fold one month's evaluation into the campaign counters (pure on the
    campaign object): satisfied months accumulate, a failed month resets
    the consecutive counter to 0; the record is appended to history."""
    if record["satisfied"]:
        campaign.months_satisfied = (campaign.months_satisfied or 0) + 1
    else:
        campaign.months_satisfied = 0
    history = list(campaign.monthly_history or [])
    history.append(record)
    campaign.monthly_history = history
    campaign.last_evaluated_month = record["month"] + 1


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _lock_station(db: Session, station_id) -> Station:
    station = (
        db.query(Station)
        .filter(Station.id == station_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if station is None:
        raise PortOwnershipError(404, "Station not found")
    return station


def _lock_players_ascending(db: Session, player_ids: Iterable) -> Dict[uuid.UUID, Player]:
    """Lock player rows FOR UPDATE in ascending id order (deadlock contract).
    populate_existing() forces fresh attribute state under the lock."""
    out: Dict[uuid.UUID, Player] = {}
    for pid in sorted(set(player_ids), key=str):
        player = (
            db.query(Player)
            .filter(Player.id == pid)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if player is None:
            raise PortOwnershipError(404, "Player not found")
        out[player.id] = player
    return out


def _wall_cutoff(days: float, now: Optional[datetime] = None) -> datetime:
    """Wall-clock instant `days` CANONICAL days in the past (scaled_deadline
    with negative hours walks backwards through the same scaling)."""
    return game_time.scaled_deadline(-days * 24.0, start=now)


def _month_bounds(anchor: datetime, month_index: int) -> Tuple[datetime, datetime]:
    """Wall-clock [start, end) of scaled month `month_index` from `anchor`."""
    start = game_time.scaled_deadline(month_index * MONTH_HOURS, start=anchor)
    end = game_time.scaled_deadline((month_index + 1) * MONTH_HOURS, start=anchor)
    return start, end


def _station_revenue(db: Session, station_id, since: datetime, until: Optional[datetime] = None) -> int:
    """Gross BUY+SELL MarketTransaction volume at the station in the window."""
    q = db.query(func.coalesce(func.sum(MarketTransaction.total_value), 0)).filter(
        MarketTransaction.station_id == station_id,
        MarketTransaction.transaction_type.in_([TransactionType.BUY, TransactionType.SELL]),
        MarketTransaction.timestamp >= since,
    )
    if until is not None:
        q = q.filter(MarketTransaction.timestamp < until)
    return int(q.scalar() or 0)


def _acquisition_cost(station: Station) -> int:
    """Acquisition cost basis: ownership JSONB written on transfer, falling
    back to acquisition_requirements['base_price'] for legacy ownership."""
    ownership = station.ownership or {}
    cost = ownership.get("acquisition_cost")
    if isinstance(cost, (int, float)) and cost > 0:
        return int(cost)
    reqs = station.acquisition_requirements or {}
    base = reqs.get("base_price", 500_000)
    return int(base) if base else 500_000


def _transfer_station(
    db: Session,
    station: Station,
    new_owner: Player,
    price: int,
    now: datetime,
    method: str,
) -> None:
    """Move ownership + association rows; treasury CONVEYS (stays on the
    station row). Caller holds the station lock and the player locks."""
    db.execute(
        player_stations.delete().where(player_stations.c.station_id == station.id)
    )
    db.execute(
        player_stations.insert().values(
            player_id=new_owner.id, station_id=station.id, acquired_at=now
        )
    )
    station.owner_id = new_owner.id
    station.ownership = {
        "player_id": str(new_owner.id),
        "acquired_at": now.isoformat(),
        "acquisition_cost": price,
        "acquisition_method": method,
    }
    flag_modified(station, "ownership")

    # Owner-change guard: every OTHER active campaign against this station
    # was building eligibility against the PREVIOUS owner — the new owner
    # gets a fresh campaign run (reset to 'building'), never inherits an
    # opponent's accumulated months or a live counter clock.
    other_campaigns = (
        db.query(TakeoverCampaign)
        .filter(
            TakeoverCampaign.station_id == station.id,
            TakeoverCampaign.status.in_(["building", "eligible", "countered"]),
            TakeoverCampaign.challenger_id != new_owner.id,
        )
        .all()
    )
    for other in other_campaigns:
        other.status = "building"
        other.months_satisfied = 0
        other.counter_expires_at = None
        logger.info(
            "Campaign %s reset to 'building': station %s changed owner",
            other.id, station.id,
        )


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def compute_price(db: Session, station: Station, now: Optional[datetime] = None) -> int:
    """Canon list-price formula (see module docstring for the documented
    interpretation): the BUSINESS component (class base x region modifier
    + one scaled month of trailing-90-canonical-day revenue + service
    upgrades) is clamped to [250k, 2M]; the treasury balance is then added
    ON TOP of the clamp — the treasury conveys 1:1 with the sale, so
    pricing it inside the clamp invited treasury arbitrage at the
    ceiling."""
    now = now or datetime.now(UTC)
    station_class = getattr(station.station_class, "value", station.station_class)
    base = CLASS_BASE_PRICE.get(station_class)
    if base is None:
        raise PortOwnershipError(400, f"Class {station_class} stations are not purchasable")

    modifier = 1.0
    region = getattr(station, "region", None)
    if region is not None and getattr(region, "economic_specialization", None):
        modifier = REGION_SPECIALIZATION_MODIFIER

    revenue_90 = _station_revenue(db, station.id, _wall_cutoff(REVENUE_WINDOW_DAYS, now))
    monthly_revenue_component = revenue_90 // 3  # one scaled month of gross trade

    upgrades = sum(
        SERVICE_UPGRADE_VALUE for enabled in (station.services or {}).values() if enabled
    )

    business = base * modifier + monthly_revenue_component + upgrades
    return business_price_with_treasury(business, station.treasury_balance or 0)


# ---------------------------------------------------------------------------
# Listing and sealed-bid purchase
# ---------------------------------------------------------------------------

def list_station(
    db: Session,
    station: Station,
    price: Optional[int] = None,
    now: Optional[datetime] = None,
) -> StationListing:
    """Put an unowned, purchasable station on the market with a 24
    canonical-hour grace window.

    `price` is DEV/ADMIN-ONLY (seed scripts, admin tooling): the public
    listing path (the create_listing adapter the router calls) always
    computes the canon price — caller-supplied prices would let players
    list stations below their computed value."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)

    if not is_listable(station):
        raise PortOwnershipError(
            400,
            f"{station.name} is not purchasable (only operational class "
            f"1-3 / 6-11 stations that are not spacedocks, TradeDocks, "
            f"quest hubs, or faction headquarters can be listed)",
        )
    if station.owner_id is not None:
        raise PortOwnershipError(400, f"{station.name} already has an owner")

    existing = (
        db.query(StationListing)
        .filter(StationListing.station_id == station.id, StationListing.status == "open")
        .first()
    )
    if existing is not None:
        raise PortOwnershipError(400, f"{station.name} is already listed")

    if price is None:
        price = compute_price(db, station, now)
    else:
        price = clamp_price(price)

    listing = StationListing(
        station_id=station.id,
        price=price,
        listed_at=now,
        grace_expires_at=game_time.scaled_deadline(GRACE_HOURS, start=now),
        status="open",
    )
    db.add(listing)
    db.flush()
    logger.info("Station listed: %s at %s credits (listing %s)", station.id, price, listing.id)
    return listing


def place_offer(
    db: Session,
    listing: StationListing,
    player: Player,
    bid: int,
    now: Optional[datetime] = None,
) -> PurchaseOffer:
    """Escrow a sealed bid against an open listing. Trusted+ reputation
    gate, one offer per player per listing, offers must be at or above the
    list price, and you cannot bid on a station you own."""
    now = now or datetime.now(UTC)
    # Lazy engine: a read past the grace window settles the auction first.
    resolve_listing(db, listing, now)

    station = _lock_station(db, listing.station_id)
    if listing.status != "open" or now >= _aware(listing.grace_expires_at):
        raise PortOwnershipError(400, "This listing is no longer accepting offers")
    if station.owner_id == player.id:
        raise PortOwnershipError(400, "You already own this station")
    if not tier_allows_purchase(player.reputation_tier):
        raise PortOwnershipError(
            403,
            f"Station purchase requires '{MIN_BUYER_TIER}' reputation or "
            f"better; you are '{player.reputation_tier}'",
        )
    if bid < listing.price:
        raise PortOwnershipError(
            400, f"Offers must meet the {listing.price:,}-credit list price"
        )
    existing = (
        db.query(PurchaseOffer)
        .filter(
            PurchaseOffer.listing_id == listing.id,
            PurchaseOffer.player_id == player.id,
        )
        .first()
    )
    if existing is not None:
        raise PortOwnershipError(400, "You already have an offer on this listing")

    locked = _lock_players_ascending(db, [player.id])
    player = locked[player.id]
    if player.credits < bid:
        raise PortOwnershipError(
            400, f"Insufficient credits: bid is {bid:,}, you have {player.credits:,}"
        )
    # Escrow: debit now; refunded if the offer loses.
    player.credits -= bid

    offer = PurchaseOffer(
        listing_id=listing.id,
        player_id=player.id,
        bid=bid,
        created_at=now,
        status="pending",
    )
    db.add(offer)
    db.flush()
    logger.info("Purchase offer: %s bid %s on listing %s", player.id, bid, listing.id)
    return offer


def resolve_listing(
    db: Session, listing: StationListing, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """LAZY auction settlement, idempotent. Past the grace window: a single
    offer sells at LIST price (any escrow above it is refunded); multiple
    offers resolve as a first-price sealed-bid auction (highest bid wins,
    ties to the earliest offer, losers refunded). No offers cancels the
    listing. Sale proceeds for an unowned station are a credit sink; the
    treasury conveys with the station."""
    now = now or datetime.now(UTC)
    # Serialization point: station lock first, then re-read the listing
    # fresh under it (populate_existing — construction advance pattern).
    station = _lock_station(db, listing.station_id)
    listing = (
        db.query(StationListing)
        .filter(StationListing.id == listing.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if listing is None:
        raise PortOwnershipError(404, "Listing not found")

    if listing.status != "open":
        return {"listing_id": str(listing.id), "status": listing.status}
    if now < _aware(listing.grace_expires_at):
        return {"listing_id": str(listing.id), "status": "open"}

    offers: List[PurchaseOffer] = (
        db.query(PurchaseOffer)
        .filter(PurchaseOffer.listing_id == listing.id, PurchaseOffer.status == "pending")
        .all()
    )

    # Station-state re-check under the lock: a station that gained an owner
    # (e.g. via takeover settlement) or was destroyed while the listing's
    # grace window ran can no longer be sold — cancel the listing and
    # refund EVERY pending escrow instead of transferring.
    if station.owner_id is not None or getattr(station, "is_destroyed", False):
        if offers:
            players = _lock_players_ascending(db, [o.player_id for o in offers])
            for offer in offers:
                players[offer.player_id].credits += offer.bid
                offer.status = "refunded"
        listing.status = "cancelled"
        db.flush()
        logger.info(
            "Listing %s cancelled at resolution (station %s owned/destroyed); "
            "%d offer(s) refunded",
            listing.id, station.id, len(offers),
        )
        return {
            "listing_id": str(listing.id),
            "status": "cancelled",
            "refunded_offers": len(offers),
        }

    if not offers:
        listing.status = "cancelled"
        db.flush()
        logger.info("Listing %s expired with no offers; cancelled", listing.id)
        return {"listing_id": str(listing.id), "status": "cancelled"}

    winner_offer, loser_offers = pick_winner(offers)
    # Single offer = sale at LIST price; multi = winner pays their bid.
    sale_price = listing.price if len(offers) == 1 else winner_offer.bid

    players = _lock_players_ascending(db, [o.player_id for o in offers])

    # Refund losers their full escrow; refund the winner any escrow above
    # the sale price (single-offer case where bid > list price).
    for offer in loser_offers:
        players[offer.player_id].credits += offer.bid
        offer.status = "refunded"
    overpay = winner_offer.bid - sale_price
    if overpay > 0:
        players[winner_offer.player_id].credits += overpay
    winner_offer.status = "won"

    _transfer_station(
        db, station, players[winner_offer.player_id], sale_price, now, method="purchase"
    )
    listing.status = "sold"
    db.flush()

    logger.info(
        "Listing %s sold: station %s -> player %s for %s credits (%d offers)",
        listing.id, station.id, winner_offer.player_id, sale_price, len(offers),
    )
    return {
        "listing_id": str(listing.id),
        "status": "sold",
        "winner_id": str(winner_offer.player_id),
        "sale_price": sale_price,
        "offers": len(offers),
    }


# ---------------------------------------------------------------------------
# Owner powers
# ---------------------------------------------------------------------------

def _require_owner(station: Station, owner: Player) -> None:
    if station.owner_id != owner.id:
        raise PortOwnershipError(403, "Only the station owner can do that")


def set_tax_rate(db: Session, station: Station, owner: Player, rate: float) -> Dict[str, Any]:
    """Owner lever: trade tax rate within canon bounds [0.0, 0.25]."""
    station = _lock_station(db, station.id)
    _require_owner(station, owner)
    if not (MIN_TAX_RATE <= rate <= MAX_TAX_RATE):
        raise PortOwnershipError(
            400, f"Tax rate must be between {MIN_TAX_RATE:.2f} and {MAX_TAX_RATE:.2f}"
        )
    station.tax_rate = float(rate)
    db.flush()
    logger.info("Station %s tax rate set to %.4f by %s", station.id, rate, owner.id)
    return {"station_id": str(station.id), "tax_rate": station.tax_rate}


def withdraw_treasury(
    db: Session, station: Station, owner: Player, amount: int
) -> Dict[str, Any]:
    """Withdraw from the station treasury to the owner (solo owner only
    this pass — no co-ownership shares yet)."""
    station = _lock_station(db, station.id)
    _require_owner(station, owner)
    if amount <= 0:
        raise PortOwnershipError(400, "Withdrawal amount must be positive")
    if amount > (station.treasury_balance or 0):
        raise PortOwnershipError(
            400,
            f"Treasury holds {station.treasury_balance or 0:,} credits; "
            f"cannot withdraw {amount:,}",
        )
    locked = _lock_players_ascending(db, [owner.id])
    owner = locked[owner.id]
    station.treasury_balance = (station.treasury_balance or 0) - amount
    owner.credits += amount
    db.flush()
    logger.info("Treasury withdrawal: %s credits from station %s to %s", amount, station.id, owner.id)
    return {
        "station_id": str(station.id),
        "withdrawn": amount,
        "treasury_balance": station.treasury_balance,
        "credits": owner.credits,
    }


def revenue_summary(db: Session, station: Station, days: int = 30) -> Dict[str, Any]:
    """Revenue ledger from MarketTransaction aggregation over the trailing
    `days` CANONICAL days: per-type counts/volumes plus an estimate of tax
    collected at the CURRENT tax rate (historical rates are not snapshotted)."""
    if days <= 0:
        raise PortOwnershipError(400, "days must be positive")
    cutoff = _wall_cutoff(days)
    rows = (
        db.query(
            MarketTransaction.transaction_type,
            func.count(MarketTransaction.id),
            func.coalesce(func.sum(MarketTransaction.total_value), 0),
        )
        .filter(
            MarketTransaction.station_id == station.id,
            MarketTransaction.timestamp >= cutoff,
            MarketTransaction.transaction_type.in_([TransactionType.BUY, TransactionType.SELL]),
        )
        .group_by(MarketTransaction.transaction_type)
        .all()
    )
    by_type = {
        t.value: {"transactions": int(count), "volume": int(volume)}
        for t, count, volume in rows
    }
    gross = sum(v["volume"] for v in by_type.values())
    return {
        "station_id": str(station.id),
        "window_canonical_days": days,
        "by_type": by_type,
        "gross_volume": gross,
        "tax_rate": station.tax_rate,
        "estimated_tax_collected": int(gross * (station.tax_rate or 0.0)),
        "treasury_balance": station.treasury_balance or 0,
    }


# ---------------------------------------------------------------------------
# Takeover engine
# ---------------------------------------------------------------------------

def launch_campaign(
    db: Session, station: Station, challenger: Player, now: Optional[datetime] = None
) -> TakeoverCampaign:
    """Open an economic-takeover campaign against an OWNED station. The
    owner cannot challenge their own station; one active campaign per
    challenger per station."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    if station.owner_id is None:
        raise PortOwnershipError(
            400, "Unowned stations are bought on the open market, not taken over"
        )
    if station.owner_id == challenger.id:
        raise PortOwnershipError(400, "You cannot launch a takeover of your own station")
    active = (
        db.query(TakeoverCampaign)
        .filter(
            TakeoverCampaign.station_id == station.id,
            TakeoverCampaign.challenger_id == challenger.id,
            TakeoverCampaign.status.in_(["building", "eligible", "countered", "disputed"]),
        )
        .first()
    )
    if active is not None:
        raise PortOwnershipError(400, "You already have an active campaign on this station")

    campaign = TakeoverCampaign(
        station_id=station.id,
        challenger_id=challenger.id,
        started_at=now,
        months_satisfied=0,
        last_evaluated_month=0,
        status="building",
        monthly_history=[],
    )
    db.add(campaign)
    db.flush()
    logger.info("Takeover campaign launched: %s vs station %s", challenger.id, station.id)
    return campaign


def monthly_volume(
    db: Session,
    station: Station,
    month_index: int,
    anchor: datetime,
    player: Optional[Player] = None,
    player_id=None,
) -> int:
    """Gross BUY+SELL volume at the station during scaled month
    `month_index` (30 canonical days each, anchored at `anchor`),
    optionally restricted to one player (pass either the ORM `player`
    or a bare `player_id`)."""
    start, end = _month_bounds(_aware(anchor), month_index)
    q = db.query(func.coalesce(func.sum(MarketTransaction.total_value), 0)).filter(
        MarketTransaction.station_id == station.id,
        MarketTransaction.transaction_type.in_([TransactionType.BUY, TransactionType.SELL]),
        MarketTransaction.timestamp >= start,
        MarketTransaction.timestamp < end,
    )
    pid = player_id if player_id is not None else (player.id if player is not None else None)
    if pid is not None:
        q = q.filter(MarketTransaction.player_id == pid)
    return int(q.scalar() or 0)


def _month_hostility(
    db: Session, station_id, challenger_id, start: datetime, end: datetime
) -> bool:
    """Hostile-pricing test for one month (documented v1 interpretation):
    the challenger's average SELL unit price undercuts the prevailing
    station-pays price — avg(station_buy_price) across the month's
    snapshotted transactions x HOSTILE_UNDERCUT_FACTOR (0.97). Months with
    NO snapshots fall back to hostile=True (volume share is the primary
    gate in v1; see hostility_verdict)."""
    sell_row = (
        db.query(
            func.coalesce(func.sum(MarketTransaction.total_value), 0),
            func.coalesce(func.sum(MarketTransaction.quantity), 0),
        )
        .filter(
            MarketTransaction.station_id == station_id,
            MarketTransaction.player_id == challenger_id,
            MarketTransaction.transaction_type == TransactionType.SELL,
            MarketTransaction.timestamp >= start,
            MarketTransaction.timestamp < end,
        )
        .first()
    )
    sell_value, sell_qty = int(sell_row[0] or 0), int(sell_row[1] or 0)
    challenger_avg_sell = (sell_value / sell_qty) if sell_qty > 0 else None

    avg_station_buy = (
        db.query(func.avg(MarketTransaction.station_buy_price))
        .filter(
            MarketTransaction.station_id == station_id,
            MarketTransaction.timestamp >= start,
            MarketTransaction.timestamp < end,
            MarketTransaction.station_buy_price.isnot(None),
        )
        .scalar()
    )
    return hostility_verdict(
        challenger_avg_sell,
        float(avg_station_buy) if avg_station_buy is not None else None,
    )


def _month_stats(
    db: Session, station: Station, campaign: TakeoverCampaign, month_index: int
) -> Dict[str, Any]:
    """Evaluate one completed scaled month: volumes, share, hostility."""
    anchor = _aware(campaign.started_at)
    start, end = _month_bounds(anchor, month_index)
    station_vol = monthly_volume(db, station, month_index, anchor)
    challenger_vol = int(
        db.query(func.coalesce(func.sum(MarketTransaction.total_value), 0))
        .filter(
            MarketTransaction.station_id == station.id,
            MarketTransaction.player_id == campaign.challenger_id,
            MarketTransaction.transaction_type.in_([TransactionType.BUY, TransactionType.SELL]),
            MarketTransaction.timestamp >= start,
            MarketTransaction.timestamp < end,
        )
        .scalar()
        or 0
    )
    share = (challenger_vol / station_vol) if station_vol > 0 else 0.0
    hostile = _month_hostility(db, station.id, campaign.challenger_id, start, end)
    return {
        "month": month_index,
        "station_volume": station_vol,
        "challenger_volume": challenger_vol,
        "share": round(share, 4),
        "hostile": hostile,
        "satisfied": month_satisfied(share, hostile),
    }


def evaluate_campaign(
    db: Session, campaign: TakeoverCampaign, now: Optional[datetime] = None
) -> TakeoverCampaign:
    """THE lazy takeover engine: catch up every completed scaled month
    since last_evaluated_month, flip to 'eligible' (opening the owner's 7
    canonical-day counter window from NOW) at 3 consecutive satisfied
    months, and settle an expired counter window. Idempotent; called from
    every takeover read/mutation."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, campaign.station_id)
    campaign = (
        db.query(TakeoverCampaign)
        .filter(TakeoverCampaign.id == campaign.id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if campaign is None:
        raise PortOwnershipError(404, "Campaign not found")

    if campaign.status in ("building", "countered"):
        # A challenger who came to own the station mid-campaign has nothing
        # left to take over.
        if station.owner_id == campaign.challenger_id:
            campaign.status = "failed"
            campaign.dispute_reason = "challenger acquired the station by other means"
            db.flush()
            return campaign
        completed = int(game_time.canonical_hours_since(campaign.started_at, now) // MONTH_HOURS)
        # Bounded catch-up: a long-dormant campaign batch-skips the older
        # months in ONE step (counter reset, single history record) and only
        # evaluates the trailing CATCHUP_EVAL_LIMIT months individually.
        skipped, months_to_evaluate = catch_up_plan(campaign.last_evaluated_month, completed)
        if skipped is not None:
            first_skipped, last_skipped = skipped
            campaign.months_satisfied = 0
            history = list(campaign.monthly_history or [])
            history.append({
                "month": last_skipped,
                "skipped_months": [first_skipped, last_skipped],
                "satisfied": False,
                "note": "batch catch-up: months skipped as unsatisfied",
            })
            campaign.monthly_history = history
            campaign.last_evaluated_month = last_skipped + 1
            flag_modified(campaign, "monthly_history")
        for month in months_to_evaluate:
            record = _month_stats(db, station, campaign, month)
            apply_month(campaign, record)
            flag_modified(campaign, "monthly_history")
            if campaign.months_satisfied >= TAKEOVER_MONTHS_REQUIRED:
                campaign.status = "eligible"
                # Counter window anchors at NOW (the owner must get a real
                # chance to respond regardless of lazy-evaluation delay).
                campaign.counter_expires_at = game_time.scaled_deadline(
                    COUNTER_WINDOW_HOURS, start=now
                )
                # Record WHO eligibility was earned against: if the owner
                # changes before settlement, the campaign resets instead of
                # forcing a sale on a stranger (settlement_owner_guard).
                history = list(campaign.monthly_history or [])
                history.append({"owner_at_eligibility": str(station.owner_id)})
                campaign.monthly_history = history
                flag_modified(campaign, "monthly_history")
                logger.info(
                    "Takeover campaign %s eligible (station %s); counter window to %s",
                    campaign.id, station.id, campaign.counter_expires_at,
                )
                break

    if (
        campaign.status == "eligible"
        and campaign.counter_expires_at is not None
        and now >= _aware(campaign.counter_expires_at)
    ):
        verdict = settlement_owner_guard(
            station.owner_id, _owner_at_eligibility(campaign)
        )
        if verdict == "failed":
            campaign.status = "failed"
            campaign.dispute_reason = "station lost its owner"
            campaign.counter_expires_at = None
        elif verdict == "reset":
            # New owner since eligibility: fresh campaign run.
            campaign.status = "building"
            campaign.months_satisfied = 0
            campaign.counter_expires_at = None
        else:
            _settle_forced_sale(db, station, campaign, now)

    db.flush()
    return campaign


def _owner_at_eligibility(campaign: TakeoverCampaign) -> Optional[str]:
    """Owner id (str) recorded when the campaign last became eligible, or
    None for campaigns that predate the owner-change guard."""
    for record in reversed(campaign.monthly_history or []):
        if "owner_at_eligibility" in record:
            return record["owner_at_eligibility"]
    return None


def forced_sale_price(db: Session, station: Station, now: Optional[datetime] = None) -> int:
    """Canon forced-sale price: clamp(90-day-average monthly revenue x 12 x
    condition_multiplier, acquisition_cost, 2 x acquisition_cost).
    condition_multiplier is 1.0 in v1 (station condition not modeled)."""
    now = now or datetime.now(UTC)
    revenue_90 = _station_revenue(db, station.id, _wall_cutoff(REVENUE_WINDOW_DAYS, now))
    avg_monthly = revenue_90 / 3.0  # 90 canonical days = 3 scaled months
    return forced_sale_value(avg_monthly, _acquisition_cost(station))


def _settle_forced_sale(
    db: Session, station: Station, campaign: TakeoverCampaign, now: datetime
) -> None:
    """Forced transfer at forced_sale_price: challenger pays the owner under
    ascending-player-id locks; treasury + ownership move atomically (the
    treasury is a station column, so it conveys with the row). A challenger
    who cannot pay FAILS the campaign. Caller holds the station lock."""
    price = forced_sale_price(db, station, now)
    owner_id = station.owner_id
    players = _lock_players_ascending(db, [campaign.challenger_id, owner_id])
    challenger = players[campaign.challenger_id]
    owner = players[owner_id]

    if challenger.credits < price:
        campaign.status = "failed"
        campaign.dispute_reason = (
            f"challenger could not pay the {price:,}-credit forced-sale price"
        )
        logger.info("Takeover campaign %s failed: challenger cannot pay %s", campaign.id, price)
        return

    challenger.credits -= price
    owner.credits += price
    _transfer_station(db, station, challenger, price, now, method="takeover")
    campaign.status = "transferred"
    campaign.counter_expires_at = None
    logger.info(
        "Takeover transfer: station %s -> %s for %s credits (campaign %s)",
        station.id, challenger.id, price, campaign.id,
    )


def _require_counterable(
    db: Session, campaign: TakeoverCampaign, owner: Player, now: datetime
) -> Tuple[Station, TakeoverCampaign]:
    """Common gate for counter actions: lazy-settle first, then require an
    'eligible' campaign, the real owner, and a live counter window."""
    campaign = evaluate_campaign(db, campaign, now)
    station = _lock_station(db, campaign.station_id)
    if campaign.status != "eligible":
        raise PortOwnershipError(
            400, f"No counter is available (campaign status: '{campaign.status}')"
        )
    _require_owner(station, owner)
    return station, campaign


def counter_accept(
    db: Session, campaign: TakeoverCampaign, owner: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Owner accepts: immediate forced sale at forced_sale_price."""
    now = now or datetime.now(UTC)
    station, campaign = _require_counterable(db, campaign, owner, now)
    _settle_forced_sale(db, station, campaign, now)
    db.flush()
    return {"campaign_id": str(campaign.id), "status": campaign.status}


def counter_match(
    db: Session, campaign: TakeoverCampaign, owner: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Owner matches: compares the owner's trade volume against the
    challenger's in the CURRENT IN-PROGRESS scaled month, BOTH recomputed
    live via monthly_volume (never read from possibly-missing history —
    and never from a completed month, which the challenger by definition
    already won, making the match unwinnable). Success resets the campaign
    clock (status 'countered', months_satisfied 0, counter window closed)
    and evaluation continues from the NEXT month."""
    now = now or datetime.now(UTC)
    station, campaign = _require_counterable(db, campaign, owner, now)

    anchor = _aware(campaign.started_at)
    month = current_month_index(campaign.started_at, now)
    challenger_vol = monthly_volume(
        db, station, month, anchor, player_id=campaign.challenger_id
    )
    owner_vol = monthly_volume(db, station, month, anchor, player_id=owner.id)
    if owner_vol < challenger_vol:
        raise PortOwnershipError(
            400,
            f"Match failed: your volume this month is {owner_vol:,} vs the "
            f"challenger's {challenger_vol:,} — the counter window keeps running",
        )
    campaign.status = "countered"
    campaign.months_satisfied = 0
    campaign.counter_expires_at = None
    # The matched (in-progress) month never re-counts for the challenger:
    # evaluation resumes from the NEXT month.
    campaign.last_evaluated_month = month + 1
    db.flush()
    logger.info("Takeover campaign %s matched by owner %s; clock reset", campaign.id, owner.id)
    return {
        "campaign_id": str(campaign.id),
        "status": campaign.status,
        "month": month,
        "owner_volume": owner_vol,
        "challenger_volume": challenger_vol,
    }


def counter_dispute(
    db: Session, campaign: TakeoverCampaign, owner: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Owner disputes (v1 auto-arbitration): if >80% of the challenger's
    volume across the evaluated campaign months is self-cancelling buy/sell
    pairs, the campaign FAILS as bot-farming; otherwise the dispute is
    rejected and the counter window keeps running."""
    now = now or datetime.now(UTC)
    station, campaign = _require_counterable(db, campaign, owner, now)

    anchor = _aware(campaign.started_at)
    start, _ = _month_bounds(anchor, max(0, campaign.last_evaluated_month - TAKEOVER_MONTHS_REQUIRED))
    _, end = _month_bounds(anchor, campaign.last_evaluated_month - 1)

    rows = (
        db.query(
            MarketTransaction.commodity,
            MarketTransaction.transaction_type,
            func.coalesce(func.sum(MarketTransaction.total_value), 0),
        )
        .filter(
            MarketTransaction.station_id == station.id,
            MarketTransaction.player_id == campaign.challenger_id,
            MarketTransaction.transaction_type.in_([TransactionType.BUY, TransactionType.SELL]),
            MarketTransaction.timestamp >= start,
            MarketTransaction.timestamp < end,
        )
        .group_by(MarketTransaction.commodity, MarketTransaction.transaction_type)
        .all()
    )
    buys: Dict[str, int] = {}
    sells: Dict[str, int] = {}
    for commodity, ttype, value in rows:
        target = buys if ttype == TransactionType.BUY else sells
        target[commodity] = target.get(commodity, 0) + int(value)

    fraction = self_cancelling_fraction(buys, sells)
    if fraction > BOT_FARM_FRACTION:
        campaign.status = "failed"
        campaign.dispute_reason = (
            f"bot-farming: {fraction:.0%} of challenger volume was "
            f"self-cancelling buy/sell pairs"
        )
        verdict = "upheld"
    else:
        # Rejected dispute: the campaign stays eligible and the counter
        # window keeps ticking.
        campaign.dispute_reason = (
            f"dispute rejected: only {fraction:.0%} self-cancelling volume "
            f"(threshold {BOT_FARM_FRACTION:.0%})"
        )
        verdict = "rejected"
    db.flush()
    logger.info("Takeover dispute %s on campaign %s (%.0f%%)", verdict, campaign.id, fraction * 100)
    return {
        "campaign_id": str(campaign.id),
        "status": campaign.status,
        "verdict": verdict,
        "self_cancelling_fraction": round(fraction, 4),
    }


def expire_counter(
    db: Session, campaign: TakeoverCampaign, now: Optional[datetime] = None
) -> TakeoverCampaign:
    """Lazy counter-window expiry: no owner response by the deadline forces
    the sale at forced_sale_price (delegates to the same settlement path
    evaluate_campaign uses)."""
    return evaluate_campaign(db, campaign, now)


# ---------------------------------------------------------------------------
# Router adapter surface
#
# Every function here returns a plain JSON-safe dict — NEVER an ORM object.
# The router spreads these dicts into responses and commits afterwards, and
# a commit expires loaded ORM state, so payloads MUST be built from live
# attributes BEFORE the router's commit. No function here commits.
# ---------------------------------------------------------------------------

_ACTIVE_CAMPAIGN_STATUSES = ("building", "eligible", "countered", "disputed")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _aware(dt).isoformat() if dt is not None else None


def _pending_offer_count(db: Session, listing_id) -> int:
    return int(
        db.query(func.count(PurchaseOffer.id))
        .filter(
            PurchaseOffer.listing_id == listing_id,
            PurchaseOffer.status == "pending",
        )
        .scalar()
        or 0
    )


def browse_listings(db: Session, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Open station listings with price previews. Lazily resolves every
    listing whose grace window has expired before returning the open set."""
    now = now or datetime.now(UTC)
    for listing in (
        db.query(StationListing).filter(StationListing.status == "open").all()
    ):
        resolve_listing(db, listing, now)

    rows = (
        db.query(StationListing, Station)
        .join(Station, Station.id == StationListing.station_id)
        .filter(StationListing.status == "open")
        .order_by(StationListing.listed_at)
        .all()
    )
    listings = []
    for listing, station in rows:
        listings.append({
            "listing_id": str(listing.id),
            "station_id": str(station.id),
            "station_name": station.name,
            "station_class": getattr(station.station_class, "value", station.station_class),
            "price": listing.price,
            "grace_expires_at": _iso(listing.grace_expires_at),
            "offers_count": _pending_offer_count(db, listing.id),
            "status": listing.status,
        })
    return {"listings": listings}


def create_listing(db: Session, station: Station, player: Player) -> Dict[str, Any]:
    """List an unowned, listable station at the COMPUTED canon price only —
    the public path never accepts a caller-supplied price (list_station's
    price parameter is dev/admin-only)."""
    listing = list_station(db, station)
    return {
        "listing_id": str(listing.id),
        "station_id": str(station.id),
        "station_name": station.name,
        "price": listing.price,
        "grace_expires_at": _iso(listing.grace_expires_at),
        "offers_count": 0,
        "status": listing.status,
    }


def submit_offer(
    db: Session, listing: StationListing, player: Player, bid: int
) -> Dict[str, Any]:
    """place_offer wrapped to a JSON-safe dict for the router."""
    offer = place_offer(db, listing, player, bid)
    return {
        "offer_id": str(offer.id),
        "listing_id": str(offer.listing_id),
        "bid": offer.bid,
        "status": offer.status,
    }


def my_stations(db: Session, player: Player) -> Dict[str, Any]:
    """Every station the player owns, with treasury/tax state, the
    acquisition cost basis, and the trailing-30-day revenue summary."""
    stations = (
        db.query(Station)
        .filter(Station.owner_id == player.id)
        .order_by(Station.name)
        .all()
    )
    out = []
    for station in stations:
        out.append({
            "station_id": str(station.id),
            "name": station.name,
            "tax_rate": station.tax_rate,
            "treasury_balance": station.treasury_balance or 0,
            "acquisition_cost": _acquisition_cost(station),
            "revenue": revenue_summary(db, station),
        })
    return {"stations": out}


def _evaluated_active_campaigns(
    db: Session, station: Station, now: datetime
) -> List[TakeoverCampaign]:
    """All active campaigns on the station, lazily advanced first (monthly
    catch-up + counter-window expiry both run inside evaluate_campaign)."""
    campaigns = (
        db.query(TakeoverCampaign)
        .filter(
            TakeoverCampaign.station_id == station.id,
            TakeoverCampaign.status.in_(_ACTIVE_CAMPAIGN_STATUSES),
        )
        .all()
    )
    return [evaluate_campaign(db, c, now) for c in campaigns]


def takeover_status(
    db: Session, station: Station, player: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Takeover campaign state for a station, lazily evaluated first
    (expired counter windows settle via the same evaluate_campaign path
    expire_counter delegates to). Prefers the viewing player's own
    campaign; an owner with no campaign of their own sees the most
    advanced active threat."""
    now = now or datetime.now(UTC)
    campaigns = _evaluated_active_campaigns(db, station, now)

    campaign = next((c for c in campaigns if c.challenger_id == player.id), None)
    if campaign is None and campaigns:
        order = {"eligible": 0, "countered": 1, "disputed": 2, "building": 3}
        campaign = sorted(
            campaigns, key=lambda c: (order.get(c.status, 9), str(c.id))
        )[0]

    forced_price = None
    if station.owner_id is not None:
        forced_price = forced_sale_price(db, station, now)

    payload: Dict[str, Any] = {
        "station_id": str(station.id),
        "owner_id": str(station.owner_id) if station.owner_id else None,
        "is_owner": station.owner_id == player.id,
        "is_challenger": False,
        "campaign_id": None,
        "status": "none",
        "months_satisfied": 0,
        "months_required": TAKEOVER_MONTHS_REQUIRED,
        "months": [],
        "counter_expires_at": None,
        "challenger_id": None,
        "forced_sale_price": forced_price,
        "dispute_reason": None,
    }
    if campaign is not None:
        months = [
            {
                "month": r.get("month"),
                "share": r.get("share"),
                "qualifies": r.get("satisfied"),
                "challenger_volume": r.get("challenger_volume"),
                "total_volume": r.get("station_volume"),
                "hostile": r.get("hostile"),
            }
            for r in (campaign.monthly_history or [])
            if "month" in r
        ]
        payload.update({
            "campaign_id": str(campaign.id),
            "status": campaign.status,
            "months_satisfied": campaign.months_satisfied or 0,
            "months": months,
            "counter_expires_at": _iso(campaign.counter_expires_at),
            "challenger_id": str(campaign.challenger_id),
            "is_challenger": campaign.challenger_id == player.id,
            "dispute_reason": campaign.dispute_reason,
        })
    return payload


def launch_takeover(db: Session, station: Station, player: Player) -> Dict[str, Any]:
    """launch_campaign wrapped to a JSON-safe dict for the router."""
    campaign = launch_campaign(db, station, player)
    return {
        "campaign_id": str(campaign.id),
        "station_id": str(station.id),
        "status": campaign.status,
        "started_at": _iso(campaign.started_at),
        "months_satisfied": campaign.months_satisfied or 0,
        "months_required": TAKEOVER_MONTHS_REQUIRED,
    }


def counter_takeover(
    db: Session, station: Station, player: Player, action: str
) -> Dict[str, Any]:
    """Dispatch the owner's counter ('accept' | 'match' | 'dispute') to the
    station's eligible campaign (lazily evaluated first)."""
    now = datetime.now(UTC)
    campaigns = _evaluated_active_campaigns(db, station, now)
    campaign = next((c for c in campaigns if c.status == "eligible"), None)
    if campaign is None:
        raise PortOwnershipError(
            400, "No takeover campaign on this station is awaiting a counter"
        )
    if action == "accept":
        return counter_accept(db, campaign, player, now)
    if action == "match":
        return counter_match(db, campaign, player, now)
    if action == "dispute":
        return counter_dispute(db, campaign, player, now)
    raise PortOwnershipError(400, f"Unknown counter action '{action}'")


def get_station_listing_status(
    db: Session, station: Station, player: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """The rich per-station ownership/listing payload the UI consumes."""
    now = now or datetime.now(UTC)

    listing = (
        db.query(StationListing)
        .filter(
            StationListing.station_id == station.id,
            StationListing.status == "open",
        )
        .first()
    )
    if listing is not None:
        # Lazy settle: resolution refreshes the identity-mapped instance
        # (populate_existing), so a sold/cancelled listing drops out here —
        # and a sale may have just given the station an owner.
        resolve_listing(db, listing, now)
        if listing.status != "open":
            listing = None

    owner_name = None
    if station.owner_id is not None:
        owner = db.query(Player).filter(Player.id == station.owner_id).first()
        owner_name = owner.username if owner is not None else None

    offers_count = 0
    my_offer = None
    if listing is not None:
        offers_count = _pending_offer_count(db, listing.id)
        mine = (
            db.query(PurchaseOffer)
            .filter(
                PurchaseOffer.listing_id == listing.id,
                PurchaseOffer.player_id == player.id,
            )
            .first()
        )
        if mine is not None:
            my_offer = {"bid": mine.bid, "status": mine.status}

    purchasable = False
    blocked_reason: Optional[str] = None
    if station.owner_id == player.id:
        blocked_reason = "You already own this station"
    elif station.owner_id is not None:
        blocked_reason = (
            "This station is player-owned — acquire it via economic takeover"
        )
    elif not is_listable(station):
        blocked_reason = "This station class/type is not purchasable"
    elif not tier_allows_purchase(player.reputation_tier):
        blocked_reason = (
            f"Station purchase requires '{MIN_BUYER_TIER}' reputation or better"
        )
    else:
        purchasable = True

    if station.owner_id is not None:
        status = "owned"
    elif listing is not None:
        status = "listed"
    else:
        status = "unlisted"

    return {
        "station_id": str(station.id),
        "owner_id": str(station.owner_id) if station.owner_id else None,
        "owner_name": owner_name,
        "is_listed": listing is not None,
        "listing_id": str(listing.id) if listing is not None else None,
        "list_price": listing.price if listing is not None else None,
        "grace_expires_at": _iso(listing.grace_expires_at) if listing is not None else None,
        "offers_count": offers_count,
        "my_offer": my_offer,
        "purchasable": purchasable,
        "blocked_reason": blocked_reason,
        "tax_rate": station.tax_rate,
        "treasury_balance": station.treasury_balance or 0,
        "status": status,
    }
