"""
Admin message moderation endpoints
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.admin_scopes import PLAYERS_VIEW
from src.auth.dependencies import require_scope
from src.models.user import User
from src.services.message_service import MessageService
from src.models.message import Message
from src.models.player import Player

router = APIRouter(prefix="/admin/messages", tags=["admin-messages"])


class ModerateMessageRequest(BaseModel):
    action: str  # 'delete', 'flag', 'unflag'
    reason: Optional[str] = None


@router.get("/all")
async def get_all_messages(
    page: int = Query(1, ge=1),
    flagged: Optional[bool] = None,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get all messages with optional filtering for flagged messages"""
    try:
        # Eager-load sender so to_dict's sender_name is always populated from
        # this query's own join, not whatever happened to already be in the
        # session's identity map (the old lazy-load-dependent behavior).
        query = db.query(Message).options(joinedload(Message.sender))

        if flagged is not None:
            query = query.filter(Message.flagged == flagged)
        
        # Get total count
        total = query.count()
        
        # Get paginated messages
        limit = 100
        offset = (page - 1) * limit
        messages = query.order_by(Message.sent_at.desc())\
                      .limit(limit)\
                      .offset(offset)\
                      .all()
        
        return {
            "messages": [msg.to_dict() for msg in messages],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/flagged")
async def get_flagged_messages(
    page: int = Query(1, ge=1),
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get only flagged messages for review"""
    return await get_all_messages(page=page, flagged=True, admin=admin, db=db)


@router.post("/{message_id}/moderate")
async def moderate_message(
    message_id: UUID,
    request: ModerateMessageRequest,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Moderate a message (delete, flag, or unflag)"""
    try:
        # Validate action
        if request.action not in ['delete', 'flag', 'unflag']:
            raise HTTPException(
                status_code=400,
                detail="Invalid action. Must be 'delete', 'flag', or 'unflag'"
            )
        
        success = await MessageService.moderate_message(
            db=db,
            message_id=message_id,
            action=request.action,
            moderator_id=admin.id,
            reason=request.reason
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Message not found")
        
        return {"success": True}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_message_statistics(
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db)
):
    """Get messaging system statistics"""
    try:
        from sqlalchemy import func
        from datetime import datetime, timedelta
        
        # Total messages
        total_messages = db.query(func.count(Message.id)).scalar()
        
        # Messages today
        today = datetime.utcnow().date()
        messages_today = db.query(func.count(Message.id))\
                          .filter(func.date(Message.sent_at) == today)\
                          .scalar()
        
        # Messages this week
        week_start = datetime.utcnow() - timedelta(days=7)
        messages_week = db.query(func.count(Message.id))\
                         .filter(Message.sent_at >= week_start)\
                         .scalar()
        
        # Flagged messages
        flagged_count = db.query(func.count(Message.id))\
                         .filter(Message.flagged == True)\
                         .scalar()
        
        # Most active senders — LEFT-joined to Player/User so a nickname
        # (the canonical Player.display_name_expr fallback) rides along in
        # one hop instead of the admin-ui resolving 10 UUIDs via the
        # heavyweight players list. fallback=None preserves this route's
        # existing behavior of surfacing `nickname: null` (not a fabricated
        # "Unknown Player" literal) when a sender's players/users row is
        # missing entirely — the ONLY value change here is the '' nickname
        # now correctly falling through to username instead of leaking as ''.
        active_senders = db.query(
            Message.sender_id,
            Player.display_name_expr(label='nickname', fallback=None),
            func.count(Message.id).label('message_count')
        ).outerjoin(Player, Player.id == Message.sender_id)\
         .outerjoin(User, User.id == Player.user_id)\
         .group_by(Message.sender_id, Player.nickname, User.username)\
         .order_by(func.count(Message.id).desc())\
         .limit(10)\
         .all()

        return {
            "total_messages": total_messages,
            "messages_today": messages_today,
            "messages_this_week": messages_week,
            "flagged_messages": flagged_count,
            "most_active_senders": [
                {"player_id": str(sender_id), "nickname": nickname, "message_count": count}
                for sender_id, nickname, count in active_senders
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))