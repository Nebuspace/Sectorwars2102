"""Player-facing Shadow Syndicate fence tab (LEG-300 first slice).

Gate-unmet and missing fence both 404 — existence must not leak
(black-market.md visibility gate; same pattern as contraband catalog).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.models.ship import Ship
from src.models.station import Station
from src.services.syndicate_fence_service import SyndicateFenceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading", tags=["syndicate-fence"])

_NOT_FOUND_REASONS = frozenset({"gate_unmet", "station_not_found"})


class FenceCargoRequest(BaseModel):
    station_id: str
    commodity: str
    quantity: int = Field(..., gt=0, le=100000)


def _active_ship(db: Session, player: Player) -> Ship:
    ship = (
        db.query(Ship)
        .filter(Ship.owner_id == player.id, Ship.is_active.is_(True))
        .first()
    )
    if ship is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active ship",
        )
    return ship


@router.get("/syndicate-fence/{station_id}")
def get_syndicate_fence(
    station_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    station = db.query(Station).filter(Station.id == station_id).first()
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    svc = SyndicateFenceService(db)
    if not svc.tab_visible_for(player, station):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {
        "station_id": str(station.id),
        "has_syndicate_fence": True,
        "services": ["cargo_fencing"],
        "payout_percent": 70,
    }


@router.post("/syndicate-fence/fence")
def post_fence_cargo(
    body: FenceCargoRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    station = db.query(Station).filter(Station.id == body.station_id).first()
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    ship = _active_ship(db, player)
    svc = SyndicateFenceService(db)
    result = svc.fence_cargo(
        player, ship, station, body.commodity.strip(), body.quantity
    )
    if not result.get("success"):
        reason = result.get("reason") or "gate_unmet"
        if reason in _NOT_FOUND_REASONS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            )
        if reason == "not_docked":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=reason
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=reason
        )
    db.commit()
    return result
