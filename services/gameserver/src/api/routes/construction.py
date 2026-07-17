"""
Ship construction (TradeDock shipyard) routes.

Canon reference: FEATURES/economy/tradedock-shipyard + ADR-0039 (sw2102-docs).
Every reservation read runs the lazy engine (construction_service.advance) so
holds, rent, phases, claim windows and queue promotions settle on access —
there is no background worker. All endpoints are ownership-gated: players can
only see and act on their own reservations.

Presence interpretations (canon is silent): placing an order, delivering
resources, and claiming the finished ship all require being DOCKED at the
TradeDock (you hand things over in person); milestone payments, rent, and
cancellation work remotely (banking transfers).
"""
import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.construction import ConstructionReservation
from src.models.player import Player
from src.models.station import Station
from src.models.user import User
from src.services import construction_service
from src.services.construction_service import ConstructionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/construction", tags=["construction"])


class ReservationCreateRequest(BaseModel):
    station_id: str
    ship_type: str
    ship_name: Optional[str] = Field(None, max_length=100)


class DeliveryRequest(BaseModel):
    ore: int = Field(0, ge=0, le=1_000_000)
    equipment: int = Field(0, ge=0, le=1_000_000)
    organics: int = Field(0, ge=0, le=1_000_000)


class MilestoneRequest(BaseModel):
    milestone: str


class PriorityBumpRequest(BaseModel):
    tier: str


class RentRequest(BaseModel):
    days: int = Field(..., ge=1, le=construction_service.RENT_MAX_PREPAY_DAYS)


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


def _get_owned_reservation_or_404(
    db: Session, reservation_id: str, player: Player
) -> ConstructionReservation:
    """Ownership gate: a reservation that isn't yours 404s (no existence leak)."""
    try:
        res_uuid = _uuid.UUID(str(reservation_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Reservation not found")
    reservation = db.query(ConstructionReservation).filter(
        ConstructionReservation.id == res_uuid
    ).first()
    if reservation is None or reservation.player_id != player.id:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


def _require_docked_at(player: Player, station: Station, action: str) -> None:
    if not player.is_docked or player.current_port_id != station.id:
        raise HTTPException(
            status_code=400,
            detail=f"You must be docked at {station.name} to {action}",
        )


@router.get("/quotes")
async def get_quotes(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Cost/duration/resource quotes for every buildable ship at a TradeDock,
    plus live slip availability and queue length."""
    station = _get_station_or_404(db, station_id)
    try:
        # quote() locks the station and settles its pipeline lazily.
        result = construction_service.quote(db, station)
        db.commit()
        return result
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/reservations")
async def create_reservation(
    request: ReservationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Place a build order: charges the 10% deposit and enters the queue."""
    station = _get_station_or_404(db, request.station_id)
    _require_docked_at(current_player, station, "place a construction order")
    try:
        reservation = construction_service.create_reservation(
            db, station, current_player, request.ship_type, request.ship_name
        )
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Construction order placed for {request.ship_type}",
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.get("/reservations/mine")
async def get_my_reservations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """All of the player's reservations (active and historical), lazily advanced."""
    reservations = (
        db.query(ConstructionReservation)
        .filter(ConstructionReservation.player_id == current_player.id)
        .order_by(ConstructionReservation.created_at.desc())
        .all()
    )
    # Advance each station's pipeline once; advancing twice is a cheap no-op.
    # Stations are visited in ascending station-id order — a GLOBAL lock
    # order, so two players holding reservations at the same two stations
    # can't deadlock by locking them in opposite orders (gate-review).
    advanced_stations = set()
    for reservation in sorted(
        (r for r in reservations if r.state not in construction_service.TERMINAL_STATES),
        key=lambda r: str(r.station_id),
    ):
        if reservation.station_id in advanced_stations:
            continue
        try:
            construction_service.advance(db, reservation)
            advanced_stations.add(reservation.station_id)
        except ConstructionError as e:
            db.rollback()
            raise HTTPException(status_code=e.status_code, detail=e.detail)
    db.commit()
    return {
        "reservations": [
            construction_service.status_payload(db, r) for r in reservations
        ]
    }


@router.get("/reservations/{reservation_id}")
async def get_reservation(
    reservation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Full reservation status: phase progress %, ISO deadlines, rent owed,
    checkpoint shortfalls. Advances the pipeline lazily."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    try:
        if reservation.state not in construction_service.TERMINAL_STATES:
            construction_service.advance(db, reservation)
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return construction_service.status_payload(db, reservation)


@router.post("/reservations/{reservation_id}/deliver")
async def deliver_resources(
    reservation_id: str,
    request: DeliveryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """ADR-0039 atomic batch delivery from the current ship's cargo.
    Irreversible — wrong types are rejected before commit."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    station = _get_station_or_404(db, str(reservation.station_id))
    _require_docked_at(current_player, station, "deliver construction resources")
    amounts = {
        "ore": request.ore,
        "equipment": request.equipment,
        "organics": request.organics,
    }
    try:
        result = construction_service.deliver(db, reservation, current_player, amounts)
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": "Resources delivered (deliveries are irreversible)",
        **result,
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.post("/reservations/{reservation_id}/pay-milestone")
async def pay_milestone(
    reservation_id: str,
    request: MilestoneRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Pay a project milestone. Paying 'keel_laid' during the 24h hold
    confirms the slip and starts the rent clock."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    try:
        result = construction_service.pay_milestone(
            db, reservation, current_player, request.milestone
        )
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Milestone '{request.milestone}' paid",
        **result,
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.post("/reservations/{reservation_id}/bump-priority")
async def bump_priority(
    reservation_id: str,
    request: PriorityBumpRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Pay a priority-bump fee (5%/25%/60%/100% of total project cost) to
    advance a still-queued reservation ahead of unbumped/lower-tier peers."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    try:
        result = construction_service.purchase_priority_bump(
            db, reservation, current_player, request.tier
        )
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Priority bump '{request.tier}' purchased",
        **result,
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.post("/reservations/{reservation_id}/pay-rent")
async def pay_rent(
    reservation_id: str,
    request: RentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Pay slip rent for 1-30 canonical days ahead."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    try:
        result = construction_service.pay_rent(
            db, reservation, current_player, request.days
        )
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Rent paid for {request.days} day(s)",
        **result,
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.post("/reservations/{reservation_id}/claim")
async def claim_ship(
    reservation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Claim the finished build (final milestone must be paid). Ordinary ship
    reservations spawn the ship at the TradeDock's sector with the
    reservation's custom name. TradeDock-class reservations (region-funded
    construction, Task B-3) finalize the TARGET STATION in place instead —
    Max's ruling (batch-1 #3a) — no Ship is ever created for those."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    station = _get_station_or_404(db, str(reservation.station_id))
    _require_docked_at(current_player, station, "claim your ship")
    try:
        result = construction_service.claim(db, reservation, current_player)
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except ValueError as e:
        # ShipService raises ValueError when the ShipSpecification is missing.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    if isinstance(result, Station):
        return {
            "message": (
                f"{result.name} has completed construction — now a "
                f"Tier-{result.tradedock_tier} TradeDock!"
            ),
            "station": {
                "id": str(result.id),
                "name": result.name,
                "sector_id": result.sector_id,
                "tradedock_tier": result.tradedock_tier,
            },
            "reservation": construction_service.status_payload(db, reservation),
        }

    ship = result
    return {
        "message": f"{ship.name} is yours — spawned in sector {ship.sector_id}",
        "ship": {
            "id": str(ship.id),
            "name": ship.name,
            "type": ship.type.value,
            "sector_id": ship.sector_id,
        },
        "reservation": construction_service.status_payload(db, reservation),
    }


@router.post("/reservations/{reservation_id}/cancel")
async def cancel_reservation(
    reservation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Cancel before completion: refunds 50% of cash paid (70% sell-back after
    hull-complete). Delivered resources are never refunded (ADR-0039)."""
    reservation = _get_owned_reservation_or_404(db, reservation_id, current_player)
    try:
        result = construction_service.cancel(db, reservation, current_player)
        db.commit()
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Reservation cancelled — {result['refund']:,} credits refunded",
        **result,
        "reservation": construction_service.status_payload(db, reservation),
    }
