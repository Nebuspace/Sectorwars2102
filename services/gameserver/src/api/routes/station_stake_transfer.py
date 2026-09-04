"""Canon stake-transfer propose/approve/reject (LEG-4236).

POST /api/v1/stations/{id}/syndicate/stake-transfer
POST /api/v1/stations/{id}/syndicate/stake-transfer/{proposal_id}/approve
POST /api/v1/stations/{id}/syndicate/stake-transfer/{proposal_id}/reject

Does not touch governance/vote.
"""
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.station import Station
from src.models.user import User
from src.services.port_ownership_service import PortOwnershipError
from src.services import station_stake_transfer_service as stake_xfer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stations", tags=["port-ownership"])


class StakeTransferProposeRequest(BaseModel):
    to_player_id: str
    pct: int = Field(..., ge=1, le=99)


def _get_station_or_404(db: Session, station_id: str) -> Station:
    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Station not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


@router.post("/{station_id}/syndicate/stake-transfer")
async def post_propose_stake_transfer(
    station_id: str,
    request: StakeTransferProposeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Co-owner proposes transferring pct of their stake to another player."""
    station = _get_station_or_404(db, station_id)
    try:
        result = stake_xfer.propose_stake_transfer(
            db,
            station,
            current_player,
            request.to_player_id,
            request.pct,
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/{station_id}/syndicate/stake-transfer/{proposal_id}/approve")
async def post_approve_stake_transfer(
    station_id: str,
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Co-owner approves; applies when approving stake >50% of remaining."""
    station = _get_station_or_404(db, station_id)
    try:
        result = stake_xfer.approve_stake_transfer(
            db, station, current_player, proposal_id
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/{station_id}/syndicate/stake-transfer/{proposal_id}/reject")
async def post_reject_stake_transfer(
    station_id: str,
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Co-owner rejects a pending stake-transfer proposal."""
    station = _get_station_or_404(db, station_id)
    try:
        result = stake_xfer.reject_stake_transfer(
            db, station, current_player, proposal_id
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result
