"""Admin re-engagement queue (LEG-28 / OPERATIONS/retention.md).

Surfaces ``player_re_engagement_queue`` rows populated by the nightly
retention sweep so operators can filter by status and mark CONTACTED /
RESOLVED. Read: ``PLAYERS_VIEW``. Status writes: ``PLAYERS_ADJUST_REP``
(same content/ops family as other player-ops panels — no new gate).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from src.auth.admin_scopes import PLAYERS_ADJUST_REP, PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.player_analytics import PlayerReEngagement
from src.models.user import User
from src.services.admin_action_log_service import log_admin_action
from src.utils.error_handling import route_internal_error

ERR_ADMIN_RE_ENGAGEMENT_SUMMARY_FAILED = "ERR_ADMIN_RE_ENGAGEMENT_SUMMARY_FAILED"
ERR_ADMIN_RE_ENGAGEMENT_LIST_FAILED = "ERR_ADMIN_RE_ENGAGEMENT_LIST_FAILED"
ERR_ADMIN_RE_ENGAGEMENT_UPDATE_FAILED = "ERR_ADMIN_RE_ENGAGEMENT_UPDATE_FAILED"

router = APIRouter(prefix="/admin/re-engagement", tags=["admin-re-engagement"])

logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = frozenset({"OPEN", "CONTACTED", "RESOLVED"})
_WRITEABLE_STATUSES = frozenset({"CONTACTED", "RESOLVED"})


def _serialize_row(row: PlayerReEngagement) -> Dict[str, Any]:
    player = row.player
    return {
        "id": str(row.id),
        "player_id": str(row.player_id),
        "player_nickname": getattr(player, "nickname", None) if player else None,
        "signals": list(row.signals or []),
        "signal_detail": dict(row.signal_detail or {}),
        "status": row.status,
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        "computed_day": row.computed_day,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


class ReEngagementStatusUpdate(BaseModel):
    status: str = Field(..., description="CONTACTED or RESOLVED")
    note: Optional[str] = Field(None, max_length=500)


@router.get("/summary")
async def re_engagement_summary(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Counts by status for the Player Analytics retention card."""
    try:
        rows = (
            db.query(PlayerReEngagement.status, func.count(PlayerReEngagement.id))
            .group_by(PlayerReEngagement.status)
            .all()
        )
        by_status = {status_key: int(count) for status_key, count in rows}
        open_count = by_status.get("OPEN", 0)
        contacted = by_status.get("CONTACTED", 0)
        resolved = by_status.get("RESOLVED", 0)
        total = open_count + contacted + resolved
        return {
            "open": open_count,
            "contacted": contacted,
            "resolved": resolved,
            "total": total,
            # Honest operator metric: share of queue still OPEN (not a D7 retention %).
            "open_share": (open_count / total) if total else None,
        }
    except Exception:
        logger.exception("Failed to fetch re-engagement summary")
        raise route_internal_error(
            ERR_ADMIN_RE_ENGAGEMENT_SUMMARY_FAILED,
            "Failed to fetch re-engagement summary",
        )


@router.get("")
async def list_re_engagement_queue(
    status_filter: Optional[str] = Query(
        "OPEN",
        alias="status",
        description="OPEN | CONTACTED | RESOLVED | ALL",
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List re-engagement queue rows, defaulting to OPEN."""
    try:
        query = db.query(PlayerReEngagement).options(joinedload(PlayerReEngagement.player))
        normalized = (status_filter or "OPEN").upper()
        if normalized != "ALL":
            if normalized not in _ALLOWED_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status filter '{status_filter}'",
                )
            query = query.filter(PlayerReEngagement.status == normalized)

        total = query.count()
        rows = (
            query.order_by(PlayerReEngagement.computed_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "items": [_serialize_row(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "status": normalized,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to fetch re-engagement queue")
        raise route_internal_error(
            ERR_ADMIN_RE_ENGAGEMENT_LIST_FAILED,
            "Failed to fetch re-engagement queue",
        )


@router.patch("/{entry_id}")
async def update_re_engagement_status(
    entry_id: uuid.UUID,
    body: ReEngagementStatusUpdate,
    admin: User = Depends(require_scope(PLAYERS_ADJUST_REP)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Mark a queue row CONTACTED or RESOLVED."""
    try:
        new_status = body.status.upper()
        if new_status not in _WRITEABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status must be CONTACTED or RESOLVED",
            )

        row = (
            db.query(PlayerReEngagement)
            .options(joinedload(PlayerReEngagement.player))
            .filter(PlayerReEngagement.id == entry_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue entry not found")

        old_status = row.status
        if old_status == "RESOLVED" and new_status != "RESOLVED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="RESOLVED entries cannot be reopened via admin UI",
            )

        row.status = new_status
        if new_status == "RESOLVED":
            row.resolved_at = datetime.now(timezone.utc)
        else:
            row.resolved_at = None

        log_admin_action(
            db,
            actor=admin,
            scope_used=PLAYERS_ADJUST_REP,
            action="re_engagement_status_update",
            target_type="player_re_engagement",
            target_id=str(entry_id),
            payload={
                "player_id": str(row.player_id),
                "old_status": old_status,
                "new_status": new_status,
                "note": body.note,
            },
        )
        db.commit()
        db.refresh(row)
        return _serialize_row(row)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update re-engagement status")
        raise route_internal_error(
            ERR_ADMIN_RE_ENGAGEMENT_UPDATE_FAILED,
            "Failed to update re-engagement status",
        )
