"""
Tractor Beam tow routes (WO-AF).

Player-usable endpoints for the Tractor Beam tow consent flow
(FEATURES/gameplay/ships.md "Tractor Beam tow operations"; ADR-0067):

  POST /tow/request   — hauler pilot requests a tow lock on a target ship
  POST /tow/accept    — target pilot ACCEPTS the hauler's pending tow request
  POST /tow/cancel    — either party drops a still-pending request (0 turns)
  POST /tow/detach    — hauler OR towed pilot breaks an active tow (0 turns)
  GET  /tow/status    — the requester's current tow state (as hauler or towed)

Turn charges (canon): lock-on / accept / cancel / detach all cost 0 turns. The
per-move tow surcharge (tiny+1/small+2/medium+3/large+5 on warps & tunnels,
+2 flat on player gates, +5 flat on a quantum jump) is charged by the movement /
quantum services on the hauler's MOVE, not here. The client UI is out of lane.
"""

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.models.ship import Ship
from src.services.tow_service import TowService, TowError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tow", tags=["tow"])


class TowRequestBody(BaseModel):
    # The target ship to tow (the towed ship's id). The hauler is the requesting
    # player's CURRENT (active) ship.
    target_ship_id: str


class AcceptTowBody(BaseModel):
    # The hauler that issued the pending request (the towing ship's id). The
    # target is the accepting player's CURRENT (active) ship.
    hauler_id: str


def _parse_uuid(raw: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/request")
async def request_tow(
    body: TowRequestBody,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The hauler pilot requests a tow lock on a target ship in the same sector.
    No turn charge. The hauler is the requesting player's active ship; it must
    carry a Tractor Beam (tow_capable)."""
    target_uuid = _parse_uuid(body.target_ship_id)

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player or locked_player.current_ship_id is None:
        raise HTTPException(status_code=400, detail="No active ship to tow with")

    hauler = (
        db.query(Ship)
        .filter(Ship.id == locked_player.current_ship_id, Ship.owner_id == locked_player.id)
        .with_for_update()
        .first()
    )
    if not hauler:
        raise HTTPException(status_code=404, detail="Hauler ship not found")

    target = db.query(Ship).filter(Ship.id == target_uuid).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target ship not found")

    try:
        result = TowService(db).request_tow(hauler, target)
    except TowError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    return result


@router.post("/accept")
async def accept_tow(
    body: AcceptTowBody,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The target pilot accepts the hauler's pending tow request (consent). No
    turn charge. The target is the accepting player's active ship; only the
    target's owner may accept a tow on it."""
    hauler_uuid = _parse_uuid(body.hauler_id)

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player or locked_player.current_ship_id is None:
        raise HTTPException(status_code=400, detail="No active ship to be towed")

    # The accepting player must own the TARGET (the ship being towed) — consent
    # belongs to the towed pilot (ships.md:353).
    target = (
        db.query(Ship)
        .filter(Ship.id == locked_player.current_ship_id, Ship.owner_id == locked_player.id)
        .with_for_update()
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="Target ship not found or not yours")

    hauler = db.query(Ship).filter(Ship.id == hauler_uuid).with_for_update().first()
    if not hauler:
        raise HTTPException(status_code=404, detail="Hauler ship not found")

    try:
        result = TowService(db).accept_tow(hauler, target)
    except TowError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    return result


@router.post("/cancel")
async def cancel_request(
    body: AcceptTowBody,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Drop a still-pending tow request (0 turns). Either the hauler pilot OR the
    target pilot may cancel."""
    hauler_uuid = _parse_uuid(body.hauler_id)

    hauler = db.query(Ship).filter(Ship.id == hauler_uuid).with_for_update().first()
    if not hauler:
        raise HTTPException(status_code=404, detail="Hauler ship not found")

    ts = hauler.tow_state or {}
    # Authorization: the hauler's owner OR the pending request's target owner.
    is_hauler_owner = hauler.owner_id == player.id
    is_target_owner = ts.get("towed_owner_id") == str(player.id)
    if not (is_hauler_owner or is_target_owner):
        raise HTTPException(status_code=403, detail="Not authorized to cancel this request")

    try:
        result = TowService(db).cancel_request(hauler)
    except TowError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    return result


@router.post("/detach")
async def detach(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Break an active tow for 0 turns — including from IN_COMBAT (detach
    priority over combat lock, ADR-0067 S-F3). Either the HAULER pilot or the
    TOWED pilot may detach; attackers who are neither party cannot. The detacher
    is identified by their active ship: if it's the hauler, detach directly; if
    it's the towed ship, find its hauler and detach that."""
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player or locked_player.current_ship_id is None:
        raise HTTPException(status_code=400, detail="No active ship")

    my_ship = (
        db.query(Ship)
        .filter(Ship.id == locked_player.current_ship_id, Ship.owner_id == locked_player.id)
        .with_for_update()
        .first()
    )
    if not my_ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    svc = TowService(db)

    # Case 1 — my active ship IS the hauler (it holds the tow_state).
    if svc.is_actively_towing(my_ship):
        try:
            result = svc.detach(my_ship)
        except TowError as e:
            raise HTTPException(status_code=400, detail=e.message)
        db.commit()
        return result

    # Case 2 — my active ship is the TOWED ship: find its hauler and detach.
    hauler = svc.find_hauler_towing(my_ship.id)
    if hauler is not None:
        # Re-lock the hauler row before mutating its tow_state.
        hauler = db.query(Ship).filter(Ship.id == hauler.id).with_for_update().first()
        try:
            result = svc.detach(hauler)
        except TowError as e:
            raise HTTPException(status_code=400, detail=e.message)
        db.commit()
        return result

    raise HTTPException(status_code=400, detail="Your ship is not part of an active tow")


@router.get("/status")
async def tow_status(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The requester's current tow involvement: whether their active ship is a
    hauler (towing), is being towed, and the tow details."""
    if player.current_ship_id is None:
        return {"towing": None, "being_towed_by": None}

    svc = TowService(db)
    my_ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
    towing = None
    if my_ship is not None and svc.is_actively_towing(my_ship):
        towing = my_ship.tow_state

    being_towed_by = None
    hauler = svc.find_hauler_towing(player.current_ship_id)
    if hauler is not None:
        being_towed_by = {
            "hauler_id": str(hauler.id),
            "surcharge_per_move": (hauler.tow_state or {}).get("surcharge_per_move"),
        }

    return {"towing": towing, "being_towed_by": being_towed_by}
