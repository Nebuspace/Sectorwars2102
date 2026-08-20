"""Admin canon moderation actions — accept / redact / block (LEG-263).

Canon paths (FEATURES/gameplay/messaging.md § Moderation actions):
``POST /api/v1/admin/moderation/messages/{id}/{accept|redact|block}``.

Distinct from ``POST /admin/messages/{id}/moderate`` (delete/flag/unflag).
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.admin_scopes import SECURITY_ACT
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.user import User
from src.services.message_service import MessageService

router = APIRouter(
    prefix="/admin/moderation/messages",
    tags=["admin-moderation-messages"],
)


class CanonModerationBody(BaseModel):
    reason: Optional[str] = None


async def _dispatch(
    *,
    message_id: UUID,
    action: str,
    reason: Optional[str],
    admin: User,
    db: Session,
):
    result = await MessageService.moderation_canon_action(
        db=db,
        message_id=message_id,
        action=action,
        moderator_id=admin.id,
        reason=reason,
    )
    if not result.get("success"):
        reason_code = result.get("reason") or "moderation_failed"
        status = 404 if reason_code == "message_not_found" else 400
        raise HTTPException(status_code=status, detail=reason_code)
    return result


@router.post("/{message_id}/accept")
async def accept_flagged_message(
    message_id: UUID,
    body: Optional[CanonModerationBody] = None,
    admin: User = Depends(require_scope(SECURITY_ACT)),
    db: Session = Depends(get_db),
):
    """Clear flag; message stays visible. No reputation penalty."""
    return await _dispatch(
        message_id=message_id,
        action="accept",
        reason=(body.reason if body else None),
        admin=admin,
        db=db,
    )


@router.post("/{message_id}/redact")
async def redact_flagged_message(
    message_id: UUID,
    body: Optional[CanonModerationBody] = None,
    admin: User = Depends(require_scope(SECURITY_ACT)),
    db: Session = Depends(get_db),
):
    """Replace body with ``[Moderated]``; notify sender; −50 personal_reputation."""
    return await _dispatch(
        message_id=message_id,
        action="redact",
        reason=(body.reason if body else None),
        admin=admin,
        db=db,
    )


@router.post("/{message_id}/block")
async def block_flagged_message(
    message_id: UUID,
    body: Optional[CanonModerationBody] = None,
    admin: User = Depends(require_scope(SECURITY_ACT)),
    db: Session = Depends(get_db),
):
    """Hide from player reads; notify sender; −100 personal_reputation.

    2+ blocks / 30d → audit escalation marker only (LEG-DEC-157 — no
    ``account_review`` column invent).
    """
    return await _dispatch(
        message_id=message_id,
        action="block",
        reason=(body.reason if body else None),
        admin=admin,
        db=db,
    )
