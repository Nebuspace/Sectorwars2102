"""
Station-protection security-tier routes: owner-facing upgrade/downgrade
ladder + status.

Canon reference: FEATURES/economy/station-protection.md (sw2102-docs).

Service contract (mirrors routes/port_ownership.py): this router codes to
the station_security_service ADAPTER surface — every function returns a
plain JSON-safe dict and raises StationSecurityError (status_code, detail)
on invalid actions. The router owns commit/rollback. Anonymous requests are
rejected with 401 by the get_current_user/get_current_player dependencies
before any handler body runs; a non-owner upgrade/downgrade/etc. attempt is
rejected with 403 by the service's _require_owner (status read is public —
see get_security_status's docstring).

    get_security_status(db, station) -> dict           # lazy-settle, public read
    upgrade_security_tier(db, station, player) -> dict  # owner-gated, one-step
    downgrade_security_tier(db, station, player) -> dict  # owner-gated, one-step, free
"""
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.station import Station
from src.models.user import User
from src.services import station_security_service
from src.services.station_security_service import StationSecurityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/station-security", tags=["station-security"])


def _get_station_or_404(db: Session, station_id: str) -> Station:
    """Fetch a station by id, turning malformed UUIDs into a 404 instead of
    a DataError that surfaces as a generic 'Database error occurred' 500
    (matches routes/port_ownership.py's identical helper)."""
    try:
        station_uuid = _uuid.UUID(str(station_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Station not found")
    station = db.query(Station).filter(Station.id == station_uuid).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


@router.get("/stations/{station_id}")
async def get_station_security_status(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Station-protection tier state: current tier, any pending upgrade/
    downgrade, and cumulative upkeep collected. Lazily settles a completed
    pending op first. Public read (any authenticated player) — a docking
    player needs to know the tier before undocking."""
    station = _get_station_or_404(db, station_id)
    try:
        result = station_security_service.get_security_status(db, station)
        db.commit()
    except StationSecurityError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return result


@router.post("/stations/{station_id}/upgrade")
async def upgrade_station_security(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Upgrade the station's security tier by exactly one rung (owner only;
    canon costs 50,000 / 200,000 / 750,000 credits for none->basic /
    basic->standard / standard->premium). Deducts the owner's personal
    credits immediately; the tier itself flips once the canon construction
    window elapses (24h / 72h / 7d, GAME_TIME_SCALE-scaled)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = station_security_service.upgrade_security_tier(
            db, station, current_player
        )
        db.commit()
    except StationSecurityError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": (
            f"Security upgrade to {result['upgrade_to']} initiated at "
            f"{station.name} for {result['cost']:,} credits"
        ),
        **result,
    }


@router.post("/stations/{station_id}/downgrade")
async def downgrade_station_security(
    station_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Downgrade the station's security tier by exactly one rung (owner
    only; free, but takes the canon 24 canonical hours, GAME_TIME_SCALE-
    scaled, to dismiss guards/decommission drones)."""
    station = _get_station_or_404(db, station_id)
    try:
        result = station_security_service.downgrade_security_tier(
            db, station, current_player
        )
        db.commit()
    except StationSecurityError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {
        "message": f"Security downgrade to {result['downgrade_to']} initiated at {station.name}",
        **result,
    }
