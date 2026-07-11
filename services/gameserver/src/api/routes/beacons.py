"""MessageBeacon API routes -- WO-P4-play-beacon-kernel, canon:
FEATURES/gameplay/message-beacons.md:30, :41-42.

- ``POST /beacons/deploy``       -- deploy a beacon at the caller's sector.
- ``GET  /beacons/{id}/read``    -- read the full message (0 turns).
- ``POST /beacons/{id}/salvage`` -- remove + refund 250cr (1 turn).

Route owns db.commit() -- message_beacon_service is flush-only throughout.
"""
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import message_beacon_service
from src.services.message_beacon_service import BeaconError, BeaconNotFoundError

router = APIRouter(prefix="/beacons", tags=["beacons"])


class DeployBeaconRequest(BaseModel):
    sector_id: int
    message: str = Field(..., min_length=1, max_length=500)
    expiry: str = "never"
    read_once: bool = False


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}") from None


def _raise_for(exc: BeaconError) -> None:
    if isinstance(exc, BeaconNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/deploy", status_code=status.HTTP_201_CREATED)
async def deploy_beacon(
    body: DeployBeaconRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """message-beacons.md:30. Debits 5 turns + 500cr + 1 equipment cargo;
    see message_beacon_service.deploy's docstring for the full validation
    order (location/docked -> nexus-protected -> rate-limit -> rep-gate ->
    content-policy -> affordability)."""
    try:
        result = message_beacon_service.deploy(
            db, current_player.id, body.sector_id, body.message,
            expiry=body.expiry, read_once=body.read_once,
        )
    except BeaconError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return result


@router.get("/{beacon_id}/read")
async def read_beacon(
    beacon_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """message-beacons.md:41. Costs 0 turns. If the beacon was deployed
    with read_once=true, this call deletes it."""
    beacon_uuid = _parse_uuid(beacon_id, "beacon_id")
    try:
        result = message_beacon_service.read(db, beacon_uuid, current_player.id)
    except BeaconError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return result


@router.post("/{beacon_id}/salvage")
async def salvage_beacon(
    beacon_id: str,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
) -> Dict[str, Any]:
    """message-beacons.md:42. Any player may salvage (deployer included).
    Costs 1 turn, refunds 250cr; the equipment cargo is not refunded."""
    beacon_uuid = _parse_uuid(beacon_id, "beacon_id")
    try:
        result = message_beacon_service.salvage(db, beacon_uuid, current_player.id)
    except BeaconError as exc:
        db.rollback()
        _raise_for(exc)
    else:
        db.commit()
        return result
