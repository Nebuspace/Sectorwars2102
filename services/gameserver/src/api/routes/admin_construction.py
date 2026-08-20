"""Admin TradeDock / construction read API (LEG-40).

Read-only ops visibility into shipyard slips, queue depth, and reservation
detail. Reuses ``construction_service`` lazy-advance + ``status_payload`` —
does not duplicate the phase state machine. Write-side force-cancel is a
follow-up (out of v1 scope).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.user import User
from src.services import construction_service
from src.services.construction_service import ConstructionError

router = APIRouter(prefix="/admin/construction", tags=["admin-construction"])


@router.get("/tradedocks")
async def list_tradedocks(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """List every station with a TradeDock shipyard (``tradedock_tier`` set)."""
    return construction_service.admin_list_tradedocks(db)


@router.get("/tradedocks/{station_id}")
async def get_tradedock_overview(
    station_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """Slip pools, queue, and active reservations for one TradeDock."""
    try:
        result = construction_service.admin_station_overview(db, station_id)
        db.commit()
        return result
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.get("/reservations/{reservation_id}")
async def get_reservation_admin(
    reservation_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """Reservation detail (lazy-advance; no player ownership gate)."""
    try:
        result = construction_service.admin_reservation_detail(db, reservation_id)
        db.commit()
        return result
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
