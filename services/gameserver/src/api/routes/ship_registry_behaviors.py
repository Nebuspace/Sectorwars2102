"""Ship Registry behavioral routes -- report-stolen / retract-stolen-report /
abandon / claim / transfer-claim.

Canon: SYSTEMS/ship-registry.md "Reporting a ship stolen", "Abandonment",
"Legal ownership transfer". WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES shipped
report/retract-stolen. WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON
added abandon/claim. WO-BUILD-SHIP-REGISTRY-CONTESTED-TRANSFER-SALVAGE-CLAIM
adds transfer-claim (file) / transfer-claim/approve here. Trade (peer-to-peer
sale) is ADR-0089's ship-bundle trade session (src/api/routes/player_trade.py)
-- not duplicated here, see ship_registry_service's module docstring.

Business logic lives in src.services.ship_registry_service -- this file is
routing + locking + HTTP-shape translation only.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship
from src.services.station_security_service import StationSecurityError
from src.services.ship_registry_service import (
    ShipRegistryError,
    abandon_ship,
    approve_transfer_claim,
    board_ship,
    claim_abandoned_ship,
    eject_ship,
    file_transfer_claim,
    report_stolen,
    retract_stolen_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ships", tags=["ship-registry"])
# Canon's eject route is POST /api/v1/players/me/eject -- a distinct prefix
# from the rest of this file's /ships/* routes, so it needs its own router
# (matches the existing /players-prefixed precedent in gc_lapse.py).
player_router = APIRouter(prefix="/players", tags=["ship-registry"])

# ERR_* codes that mean "the request is well-formed but conflicts with
# current game state" -> 409, distinct from validation (422) or not-found
# (404). ERR_NOT_REGISTERED_OWNER is a 403 (authorization, not conflict).
_CONFLICT_CODES = {
    "ERR_ALREADY_STOLEN",
    "ERR_NOT_STOLEN",
    "ERR_BOUNTY_ALREADY_COLLECTED",
    "ERR_THIEF_IS_TEAM_MATE",
    "ERR_INSUFFICIENT_CREDITS_FOR_AUTO_BOUNTY",
    "ERR_ALREADY_ABANDONED",
    "ERR_SHIP_BORROWED",
    "ERR_NOT_ABANDONED",
    "ERR_NOT_AT_PORT",
    "ERR_ALREADY_OWNER",
    "ERR_TRANSFER_ALREADY_PENDING",
    "ERR_SHIP_STOLEN",
    "ERR_NOT_ELIGIBLE_FOR_TRANSFER",
    "ERR_INSUFFICIENT_CREDITS",
    "ERR_NO_PENDING_TRANSFER",
    "ERR_NO_CURRENT_SHIP",
    "ERR_ALREADY_IN_ESCAPE_POD",
    "ERR_INSUFFICIENT_TURNS",
    "ERR_SHIP_DESTROYED",
    "ERR_SHIP_HARMONIZING",
    "ERR_ALREADY_PILOTING",
    "ERR_SHIP_LOCKED",
}


def _raise_for(exc: ShipRegistryError) -> None:
    if exc.code == "ERR_NOT_REGISTERED_OWNER":
        http_status = status.HTTP_403_FORBIDDEN
    elif exc.code in _CONFLICT_CODES:
        http_status = status.HTTP_409_CONFLICT
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=http_status, detail={"code": exc.code, "message": exc.message})


def _get_locked_ship(db: Session, ship_id) -> Ship:
    ship = db.query(Ship).filter(Ship.id == ship_id).with_for_update().first()
    if ship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found.")
    return ship


def _get_locked_player(db: Session, player_id) -> Player:
    """Resource-before-player lock ordering: callers lock the ship row (via
    ``_get_locked_ship``) before this, never after."""
    locked = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found.")
    return locked


class ReportStolenRequest(BaseModel):
    recovery_mode: str | None = None  # "with_bounty" | "no_bounty"; None = ADR-0055 S-F4 default


@router.post("/{ship_id}/report-stolen")
async def report_stolen_route(
    ship_id: str,
    request: ReportStolenRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    # Lock the ship row first (single row, no cross-player ordering hazard --
    # the owner's own credit debit happens inside the locked bounty_service
    # call, which locks the two Player rows in its own established ascending-
    # id order).
    ship = _get_locked_ship(db, ship_id)

    try:
        result = report_stolen(db, ship=ship, owner=player, recovery_mode=request.recovery_mode)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info(
        "Ship %s reported stolen by owner %s (recovery_mode=%s, bounty=%s)",
        ship_id, player.id, result["recovery_mode"], result["bounty_id"],
    )
    return result


@router.post("/{ship_id}/retract-stolen-report")
async def retract_stolen_report_route(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    ship = _get_locked_ship(db, ship_id)

    try:
        result = retract_stolen_report(db, ship=ship, owner=player)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info(
        "Ship %s stolen report retracted by owner %s (refund=%s)",
        ship_id, player.id, result["refund"],
    )
    return result


class PortActionRequest(BaseModel):
    port_id: UUID


@router.post("/{ship_id}/abandon")
async def abandon_ship_route(
    ship_id: str,
    request: PortActionRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    ship = _get_locked_ship(db, ship_id)

    try:
        result = abandon_ship(db, ship=ship, owner=player, port_id=request.port_id)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info("Ship %s abandoned by owner %s at port %s", ship_id, player.id, request.port_id)
    return result


@router.post("/{ship_id}/claim")
async def claim_abandoned_ship_route(
    ship_id: str,
    request: PortActionRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    ship = _get_locked_ship(db, ship_id)

    try:
        result = claim_abandoned_ship(db, ship=ship, claimant=player, port_id=request.port_id)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info("Ship %s claimed by %s at port %s", ship_id, player.id, request.port_id)
    return result


@router.post("/{ship_id}/transfer-claim")
async def file_transfer_claim_route(
    ship_id: str,
    request: PortActionRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """File a contested registration-transfer claim (ship-registry.md
    "Legal ownership transfer") -- the "Salvage" acquisition method."""
    ship = _get_locked_ship(db, ship_id)

    try:
        result = file_transfer_claim(db, ship=ship, claimant=player, port_id=request.port_id)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info(
        "Ship %s transfer claim filed by %s at port %s (fee=%s, deadline=%s)",
        ship_id, player.id, request.port_id, result["fee_paid"], result["dispute_deadline"],
    )
    return result


@router.post("/{ship_id}/transfer-claim/approve")
async def approve_transfer_claim_route(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The registered owner explicitly approves a pending transfer claim,
    completing it immediately instead of waiting out the 24h window."""
    ship = _get_locked_ship(db, ship_id)

    try:
        result = approve_transfer_claim(db, ship=ship, owner=player)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info("Ship %s transfer claim approved by owner %s", ship_id, player.id)
    return result


@player_router.post("/me/eject")
async def eject_ship_route(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Voluntarily eject from the currently-piloted ship (ship-registry.md
    "Eject and board"). No ship_id -- always acts on the caller's own
    current ship."""
    locked_player = _get_locked_player(db, player.id)

    try:
        result = eject_ship(db, player=locked_player)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises

    db.commit()
    logger.info(
        "Player %s ejected from ship %s (turns_spent=%s)",
        player.id, result["ejected_ship_id"], result["turns_spent"],
    )
    return result


class BoardShipRequest(BaseModel):
    pin: str | None = None  # required unless the caller is the registered owner


@router.post("/{ship_id}/board")
async def board_ship_route(
    ship_id: str,
    request: BoardShipRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Board ``ship_id`` (ship-registry.md "Eject and board" + "Hatch pin
    lock"). Works for both a stranger's ship (pin required unless you're the
    registered owner) and one of your own parked ships -- for the common
    "switch between my own ships" case, POST /ships/{id}/set-active remains
    the existing frontend-wired path with no turn cost; this route is the
    canon-named general case, including turn-cost accounting."""
    ship = _get_locked_ship(db, ship_id)
    locked_player = _get_locked_player(db, player.id)

    try:
        result = board_ship(db, ship=ship, boarder=locked_player, pin=request.pin)
    except ShipRegistryError as exc:
        db.rollback()
        _raise_for(exc)
        return  # pragma: no cover -- _raise_for always raises
    except StationSecurityError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    db.commit()
    logger.info(
        "Ship %s boarded by %s (state=%s, turns_spent=%s, now_wanted=%s)",
        ship_id, player.id, result["state"], result["turns_spent"], result["now_wanted"],
    )
    return result
