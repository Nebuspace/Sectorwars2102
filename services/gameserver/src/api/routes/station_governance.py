"""Canon POST /api/v1/stations/{id}/governance/vote (LEG-301)."""
import logging
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.station import Station
from src.models.user import User
from src.services.port_ownership_service import PortOwnershipError
from src.services.station_governance_service import cast_governance_vote

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stations", tags=["port-ownership"])


class GovernanceVoteRequest(BaseModel):
    vote_type: str
    proposed_value: Any = None
    voter_stake_pct: int = Field(..., ge=1, le=100)
    position: str


def _get_station_or_404(db: Session, station_id: str) -> Station:
    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Station not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


@router.post("/{station_id}/governance/vote")
async def post_governance_vote(
    station_id: str,
    request: GovernanceVoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Syndicate co-owner policy vote (port-ownership.md:138-152)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = cast_governance_vote(
            db,
            station,
            current_player,
            vote_type=request.vote_type,
            proposed_value=request.proposed_value,
            voter_stake_pct=request.voter_stake_pct,
            position=request.position,
        )
        db.commit()
    except PortOwnershipError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result
