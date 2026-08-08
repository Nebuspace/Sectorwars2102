"""Admin message-beacon moderation (WO-BEACON-ADMIN-CLEAR-FLAG).

List flagged sector beacons and clear false-report hides. Mirrors
admin_messages.py scope pattern: PLAYERS_VIEW to list, SECURITY_ACT to act.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.auth.admin_scopes import PLAYERS_VIEW, SECURITY_ACT
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.user import User
from src.services import message_beacon_service
from src.services.message_beacon_service import BeaconError, BeaconNotFoundError

router = APIRouter(prefix="/admin/beacons", tags=["admin-beacons"])


@router.get("/flagged")
async def get_flagged_beacons(
    page: int = Query(1, ge=1),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """Paginated queue of player-reported (flagged) message beacons."""
    return message_beacon_service.list_flagged_beacons(db, page=page)


@router.post("/{beacon_id}/clear-flag")
async def clear_beacon_flag(
    beacon_id: UUID,
    admin: User = Depends(require_scope(SECURITY_ACT)),
    db: Session = Depends(get_db),
):
    """Clear flagged=true so the beacon reappears in sector denorm/read."""
    try:
        result = message_beacon_service.clear_flag(db, beacon_id)
        db.commit()
    except BeaconNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except BeaconError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, **result}


@router.post("/{beacon_id}/confirm-abuse")
async def confirm_beacon_abuse(
    beacon_id: UUID,
    admin: User = Depends(require_scope(SECURITY_ACT)),
    db: Session = Depends(get_db),
):
    """Confirm flagged beacon as abusive: dock deployer trust, remove row.

    Does not auto-suspend / time-ban (human-gated). False reports use
    clear-flag instead.
    """
    try:
        result = message_beacon_service.confirm_abuse(db, beacon_id)
        db.commit()
    except BeaconNotFoundError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except BeaconError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, **result}
