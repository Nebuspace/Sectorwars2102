"""
Port (station) ownership routes: listings, sealed-bid auctions, owner powers
(tax rate, treasury withdrawal, revenue ledger) and economic takeover campaigns.

Canon reference: FEATURES/economy/port-ownership (sw2102-docs).

Lazy engines (there is NO scheduler): auction resolution past the 24-canonical-
hour grace window, takeover monthly evaluation, and counter-window expiry all
settle when a read touches them — every status endpoint here calls the
corresponding lazy service function before returning, mirroring the
construction routes' advance() pattern.

Service contract: this router codes to the port_ownership_service ADAPTER
surface — every function returns a plain JSON-safe dict (never an ORM
object; the router spreads results into responses AFTER committing, and a
commit expires ORM state) and raises PortOwnershipError (status_code,
detail) on invalid actions. The router owns commit/rollback:

    browse_listings(db) -> {'listings': [...]}
    create_listing(db, station, player) -> dict        # computed price only
    submit_offer(db, listing, player, bid) -> dict     # escrowed sealed bid
    resolve_listing(db, listing) -> dict               # lazy resolve, then payload
    my_stations(db, player) -> {'stations': [...]}
    set_tax_rate(db, station, player, rate) -> dict
    withdraw_treasury(db, station, player, amount) -> dict
    takeover_status(db, station, player) -> dict       # lazy monthly evaluation
    launch_takeover(db, station, player) -> dict
    counter_takeover(db, station, player, action) -> dict
    get_station_listing_status(db, station, player) -> dict  # rich UI payload
"""
import logging
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.port_ownership import StationListing
from src.models.station import Station
from src.models.user import User
from src.services import port_ownership_service
from src.services.port_ownership_service import PortOwnershipError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/port-ownership", tags=["port-ownership"])


class OfferRequest(BaseModel):
    # Bids are escrowed (debited) at offer time; bounds match the canonical
    # price clamp [250_000, 2_000_000] so impossible bids fail fast.
    bid: int = Field(..., ge=1, le=2_000_000)


class TaxRateRequest(BaseModel):
    # Canon bounds: owners may set tax anywhere in [0.0, 0.25].
    rate: float = Field(..., ge=0.0, le=0.25)


class WithdrawRequest(BaseModel):
    amount: int = Field(..., ge=1)


class CounterRequest(BaseModel):
    action: Literal["accept", "match", "dispute"]


class MilitaryActionRequest(BaseModel):
    # Military takeover stage: declare (file intent + 24h notice), siege
    # (one combat round vs station defenders), or occupy (capture once
    # defenders are eliminated).
    action: Literal["declare", "siege", "occupy"]


def _get_station_or_404(db: Session, station_id: str) -> Station:
    """Fetch a station by id, turning malformed UUIDs into a 404 instead of
    a DataError that surfaces as a generic 'Database error occurred' 500."""
    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Station not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


def _get_listing_or_404(db: Session, listing_id: str) -> StationListing:
    """Load a listing by id; malformed or unknown UUIDs 404 (no existence
    leak, no DataError 500). The service functions take the ORM instance."""
    try:
        listing_uuid = _uuid.UUID(str(listing_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Listing not found")
    listing = db.query(StationListing).filter(StationListing.id == listing_uuid).first()
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing


@router.get("/listings")
async def browse_listings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Browse open station listings with computed price previews.

    Listings whose 24-canonical-hour grace window has expired are resolved
    lazily inside the service before the open set is returned.
    """
    try:
        result = port_ownership_service.browse_listings(db)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/list")
async def list_station(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """List an unowned, listable station for sale. The service enforces canon
    eligibility (classes 1-3 / 8-11 only; never population hubs, spacedocks,
    or TradeDocks) and computes the clamped list price."""
    station = _get_station_or_404(db, station_id)
    try:
        listing = port_ownership_service.create_listing(db, station, current_player)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"{station.name} listed for sale",
        "listing": listing,
    }


@router.get("/stations/{station_id}/listing")
async def get_station_listing(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Station-keyed listing/ownership status — the rich payload the UI
    consumes: owner, open-listing state, the viewing player's own offer,
    and whether (and why not) the station is purchasable. Lazily resolves
    an expired grace window first."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.get_station_listing_status(
            db, station, current_player
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/offer")
async def place_station_offer(
    station_id: str,
    request: OfferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Station-keyed offer: resolves the station's open listing and files
    the sealed bid against it (the UI works per-station, not per-listing)."""
    station = _get_station_or_404(db, station_id)
    listing = (
        db.query(StationListing)
        .filter(
            StationListing.station_id == station.id,
            StationListing.status == "open",
        )
        .first()
    )
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail=f"{station.name} has no open listing — it is not currently for sale",
        )
    try:
        result = port_ownership_service.submit_offer(
            db, listing, current_player, request.bid
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Offer of {request.bid:,} credits escrowed on {station.name}",
        **result,
    }


@router.post("/listings/{listing_id}/offer")
async def place_offer(
    listing_id: str,
    request: OfferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Place a sealed bid on an open listing. Funds are escrowed (debited)
    immediately; losing bids are refunded at resolution. The service enforces
    the 'Trusted'+ reputation gate."""
    listing = _get_listing_or_404(db, listing_id)
    try:
        result = port_ownership_service.submit_offer(
            db, listing, current_player, request.bid
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Offer of {request.bid:,} credits escrowed",
        **result,
    }


@router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Listing status. Lazily resolves the listing first: past the grace
    window a single offer becomes a sale at list price, multiple offers
    resolve as a sealed-bid auction (highest wins, losers refunded)."""
    listing = _get_listing_or_404(db, listing_id)
    try:
        result = port_ownership_service.resolve_listing(db, listing)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.get("/my-stations")
async def get_my_stations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """All stations the player owns: treasury balance, tax rate, and the
    MarketTransaction-derived revenue summary for each."""
    try:
        result = port_ownership_service.my_stations(db, current_player)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/tax")
async def set_tax_rate(
    station_id: str,
    request: TaxRateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Set the station's trade tax rate (owner only, canon bounds 0.0-0.25)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.set_tax_rate(
            db, station, current_player, request.rate
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Tax rate at {station.name} set to {request.rate:.0%}",
        **result,
    }


@router.post("/stations/{station_id}/withdraw")
async def withdraw_treasury(
    station_id: str,
    request: WithdrawRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Withdraw credits from the station treasury (solo owner only this pass)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.withdraw_treasury(
            db, station, current_player, request.amount
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Withdrew {request.amount:,} credits from {station.name}",
        **result,
    }


@router.get("/stations/{station_id}/takeover")
async def get_takeover_status(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Economic takeover campaign state for a station. Lazily evaluates
    pending scaled months and the owner's 7-canonical-day counter window,
    then returns campaign state, monthly share history, and the counter
    deadline (if any)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.takeover_status(db, station, current_player)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/takeover/launch")
async def launch_takeover(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Launch an economic takeover campaign against an owned station.
    Eligibility requires >50% of monthly trade volume with hostile pricing
    for 3 consecutive scaled months (evaluated lazily on reads)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.launch_takeover(db, station, current_player)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Takeover campaign launched against {station.name}",
        **result,
    }


@router.post("/stations/{station_id}/accrue-costs")
async def accrue_operating_costs(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Settle pending operating costs (lazy maintenance accrual) for an owned
    station and report whether it tipped into insolvency. Maintenance is 1% of
    acquisition cost / canonical month, pro-rated to whole elapsed days, drawn
    from the operating fund then the treasury. Three consecutive shortfall
    months auto-list the station for sale at depreciated value (reusing the
    listing path). Same lazy-on-read pattern as the listing/takeover engines;
    a scheduler may call this entry point later (wiring is a follow-up)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.accrue_operating_costs(db, station)
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/military")
async def military_takeover(
    station_id: str,
    request: MilitaryActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Military (combat) takeover path against an owned station:
    'declare' files intent and starts a 24-canonical-hour galaxy-wide notice;
    'siege' runs one combat round once the notice elapses, depleting the
    station's defenders with the attacker's drones; 'occupy' captures the
    station once defenders reach 0 — the prior owner's treasury is forfeited
    to the controlling faction (war-tax), the attacker takes a severe
    reputation penalty, and a 7-day post-capture protection window opens.
    Stations holding a Military Contract are immune."""
    station = _get_station_or_404(db, station_id)
    try:
        if request.action == "declare":
            result = port_ownership_service.declare_military_takeover(
                db, station, current_player
            )
        elif request.action == "siege":
            result = port_ownership_service.siege_military_takeover(
                db, station, current_player
            )
        else:  # occupy
            result = port_ownership_service.occupy_military_takeover(
                db, station, current_player
            )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/takeover/counter")
async def counter_takeover(
    station_id: str,
    request: CounterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Owner's response inside the 7-canonical-day counter window:
    'accept' (forced sale at the clamped valuation), 'match' (owner volume
    that month >= challenger's resets the clock), or 'dispute' (v1
    auto-arbitration against bot-farmed self-cancelling volume)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = port_ownership_service.counter_takeover(
            db, station, current_player, request.action
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result
