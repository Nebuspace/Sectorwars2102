"""Ship Registry behavioral routes -- report-stolen / retract-stolen-report /
abandon / claim.

Canon: SYSTEMS/ship-registry.md "Reporting a ship stolen", "Abandonment".
WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES shipped report/retract-stolen.
WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON adds abandon/claim here.
Trade (peer-to-peer sale) is ADR-0089's ship-bundle trade session
(src/api/routes/player_trade.py) -- not duplicated here, see
ship_registry_service's module docstring. Contested registration transfer /
salvage-claim is deferred -- see that module's docstring for why.

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
from src.services.ship_registry_service import (
    ShipRegistryError,
    abandon_ship,
    claim_abandoned_ship,
    report_stolen,
    retract_stolen_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ships", tags=["ship-registry"])

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
