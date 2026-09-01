"""Admin TradeDock / construction API (LEG-40 + LEG-339).

Read ops: shipyard slips, queue depth, reservation detail — reuses
``construction_service`` lazy-advance + ``status_payload``.

Write: admin force-cancel reuses ``construction_service.cancel`` refund math
(ADR-0039 — credits per ``cancel_refund``, resources never returned).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.user import User
from src.services import construction_service
from src.services.admin_action_log_service import log_admin_action
from src.services.construction_service import ConstructionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/construction", tags=["admin-construction"])


@router.get("/tradedocks")
async def list_tradedocks(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """List every station with a TradeDock shipyard (``tradedock_tier`` set)."""
    try:
        return construction_service.admin_list_tradedocks(db)
    except Exception:
        logger.exception("Error in list_tradedocks")
        raise HTTPException(status_code=500, detail="Failed to list tradedocks")


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


@router.post("/reservations/{reservation_id}/force-cancel")
async def force_cancel_reservation(
    reservation_id: UUID,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
):
    """Force-cancel any reservation; refunds via player cancel math (LEG-339).

    No player-ownership gate — admin auth is the gate. Refund schedule is
    ``cancel_refund`` (50% of cash paid; 70% sell-back after hull_complete).
    Delivered resources are never returned (ADR-0039).
    """
    try:
        result = construction_service.admin_force_cancel(db, reservation_id)
        log_admin_action(
            db,
            actor=admin,
            scope_used=PLAYERS_VIEW,
            action="admin_construction_force_cancel",
            target_type="construction_reservation",
            target_id=str(reservation_id),
            payload={
                "refund": result.get("refund"),
                "player_id": result.get("player_id"),
                "resources_refunded": result.get("resources_refunded", 0),
            },
        )
        db.commit()
        return {
            "message": (
                f"Reservation force-cancelled — "
                f"{result['refund']:,} credits refunded"
            ),
            **result,
        }
    except ConstructionError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
