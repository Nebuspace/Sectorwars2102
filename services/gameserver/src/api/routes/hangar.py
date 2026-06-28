"""
Carrier ship-hangar routes (WO-AE).

Player-usable endpoints for the Carrier ship-hangar consent flow
(FEATURES/gameplay/ships.md "Carrier hangar"):

  POST /hangar/{carrier_id}/dock-request  — docking pilot requests a hangar slot
  POST /hangar/{carrier_id}/accept        — Carrier captain accepts a request
  POST /hangar/{carrier_id}/cancel        — either party drops a pending request
  POST /hangar/undock                     — docked pilot resumes control (1 turn)
  POST /hangar/disembark                  — docked pilot steps off to a port (0 turns)
  GET  /hangar/{carrier_id}               — Carrier captain views hangar state

Turn charges (canon): dock = 1 turn on the docking ship (Carrier 0); undock =
1 turn on the docked pilot; disembark = 0 turns. The route layer charges turns
via spend_turns after the HangarService resolver succeeds; the service itself
only manages hangar/location state. The client UI is out of lane.
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
from src.services.hangar_service import HangarService, HangarError
from src.services.turn_service import regenerate_turns, spend_turns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hangar", tags=["hangar"])


class DockRequestBody(BaseModel):
    # Optional: defaults to the requesting player's CURRENT (active) ship.
    ship_id: str | None = None


class AcceptDockBody(BaseModel):
    ship_id: str  # which pending request to accept (the docking ship's id)


class CancelRequestBody(BaseModel):
    ship_id: str  # which pending request to drop


def _parse_uuid(raw: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/{carrier_id}/dock-request")
async def request_dock(
    carrier_id: str,
    body: DockRequestBody = DockRequestBody(),
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The docking pilot requests a hangar slot on a Carrier. No turn charge
    (the 1-turn cost is on accept). The docking ship defaults to the player's
    active ship."""
    carrier_uuid = _parse_uuid(carrier_id)

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Resolve the docking ship (defaults to the player's active ship). It must
    # be owned by the requester.
    if body.ship_id:
        ship_uuid = _parse_uuid(body.ship_id)
    else:
        ship_uuid = locked_player.current_ship_id
    if ship_uuid is None:
        raise HTTPException(status_code=400, detail="No active ship to dock")

    docking_ship = (
        db.query(Ship)
        .filter(Ship.id == ship_uuid, Ship.owner_id == locked_player.id)
        .with_for_update()
        .first()
    )
    if not docking_ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    carrier = db.query(Ship).filter(Ship.id == carrier_uuid).with_for_update().first()
    if not carrier:
        raise HTTPException(status_code=404, detail="Carrier not found")

    try:
        result = HangarService(db).request_dock(docking_ship, carrier)
    except HangarError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    return result


@router.post("/{carrier_id}/accept")
async def accept_dock(
    carrier_id: str,
    body: AcceptDockBody,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The Carrier captain accepts a pending dock request. The docking ship pays
    1 turn (Carrier 0). Only the Carrier's owner may accept."""
    carrier_uuid = _parse_uuid(carrier_id)
    ship_uuid = _parse_uuid(body.ship_id)

    # Lock the Carrier hull; only its owner may accept dock requests.
    carrier = (
        db.query(Ship)
        .filter(Ship.id == carrier_uuid, Ship.owner_id == player.id)
        .with_for_update()
        .first()
    )
    if not carrier:
        raise HTTPException(status_code=404, detail="Carrier not found or not yours")

    docking_ship = db.query(Ship).filter(Ship.id == ship_uuid).with_for_update().first()
    if not docking_ship:
        raise HTTPException(status_code=404, detail="Docking ship not found")

    # The docking pilot is the docking ship's owner — locked so the 1-turn
    # charge can't race a concurrent spend.
    if docking_ship.owner_id is None:
        raise HTTPException(status_code=400, detail="That ship has no pilot to dock")
    docking_pilot = (
        db.query(Player).filter(Player.id == docking_ship.owner_id).with_for_update().first()
    )
    if not docking_pilot:
        raise HTTPException(status_code=404, detail="Docking pilot not found")

    svc = HangarService(db)
    try:
        result, turn_cost = svc.accept_dock(carrier, docking_ship, docking_pilot)
    except HangarError as e:
        raise HTTPException(status_code=400, detail=e.message)

    # Charge the docking pilot the 1-turn dock cost (Carrier pays 0). Bring the
    # balance current first (ADR-0004 lazy regen) before the affordability check.
    regenerate_turns(db, docking_pilot)
    if docking_pilot.turns < turn_cost:
        db.rollback()
        raise HTTPException(status_code=400, detail="Docking pilot lacks the turn for the dock")
    spend_turns(docking_pilot, turn_cost)

    db.commit()
    result["turn_cost"] = turn_cost
    return result


@router.post("/{carrier_id}/cancel")
async def cancel_request(
    carrier_id: str,
    body: CancelRequestBody,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Drop a still-pending dock request (0 turns). Either the Carrier captain
    OR the docking pilot may cancel."""
    carrier_uuid = _parse_uuid(carrier_id)
    ship_uuid = _parse_uuid(body.ship_id)

    carrier = db.query(Ship).filter(Ship.id == carrier_uuid).with_for_update().first()
    if not carrier:
        raise HTTPException(status_code=404, detail="Carrier not found")

    # Authorization: the Carrier owner OR the docking ship's owner.
    docking_ship = db.query(Ship).filter(Ship.id == ship_uuid).first()
    is_carrier_owner = carrier.owner_id == player.id
    is_docking_owner = docking_ship is not None and docking_ship.owner_id == player.id
    if not (is_carrier_owner or is_docking_owner):
        raise HTTPException(status_code=403, detail="Not authorized to cancel this request")

    try:
        result = HangarService(db).cancel_request(carrier, ship_uuid)
    except HangarError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    return result


@router.post("/undock")
async def undock(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The docked pilot resumes control of their CURRENT ship in the Carrier's
    current sector. 1 turn. No Carrier consent."""
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player or locked_player.current_ship_id is None:
        raise HTTPException(status_code=400, detail="No active ship to undock")

    docked_ship = (
        db.query(Ship).filter(Ship.id == locked_player.current_ship_id).with_for_update().first()
    )
    if not docked_ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    svc = HangarService(db)
    try:
        result, turn_cost = svc.undock(docked_ship, locked_player)
    except HangarError as e:
        raise HTTPException(status_code=400, detail=e.message)

    regenerate_turns(db, locked_player)
    if locked_player.turns < turn_cost:
        db.rollback()
        raise HTTPException(status_code=400, detail="Not enough turns to undock")
    spend_turns(locked_player, turn_cost)

    db.commit()
    result["turn_cost"] = turn_cost
    return result


@router.post("/disembark")
async def disembark(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """When the Carrier is docked at a station, the passenger steps off to the
    port at 0 turns."""
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if not locked_player or locked_player.current_ship_id is None:
        raise HTTPException(status_code=400, detail="No active ship to disembark")

    docked_ship = (
        db.query(Ship).filter(Ship.id == locked_player.current_ship_id).with_for_update().first()
    )
    if not docked_ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    try:
        result, turn_cost = HangarService(db).disembark_to_port(docked_ship, locked_player)
    except HangarError as e:
        raise HTTPException(status_code=400, detail=e.message)

    db.commit()
    result["turn_cost"] = turn_cost
    return result


@router.get("/{carrier_id}")
async def get_hangar(
    carrier_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """View a Carrier's hangar state (capacity, used units, docked manifest).
    Carrier captain only."""
    carrier_uuid = _parse_uuid(carrier_id)
    carrier = (
        db.query(Ship)
        .filter(Ship.id == carrier_uuid, Ship.owner_id == player.id)
        .first()
    )
    if not carrier:
        raise HTTPException(status_code=404, detail="Carrier not found or not yours")

    svc = HangarService(db)
    hangar = carrier.hangar or HangarService.empty_hangar()
    return {
        "carrier_id": str(carrier.id),
        "capacity_units": hangar.get("capacity_units", 0),
        "used_units": svc.used_units(hangar),
        "docked": hangar.get("docked", []),
    }
