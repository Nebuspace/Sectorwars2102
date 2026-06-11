"""
Admin message moderation endpoints
"""

from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_admin_user
from src.models.user import User
from src.services.message_service import MessageService
from src.models.message import Message

router = APIRouter(prefix="/admin/messages", tags=["admin-messages"])


class ModerateMessageRequest(BaseModel):
    action: str  # 'delete', 'flag', 'unflag'
    reason: Optional[str] = None


@router.get("/all")
async def get_all_messages(
    page: int = Query(1, ge=1),
    flagged: Optional[bool] = None,
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get all messages with optional filtering for flagged messages"""
    try:
        query = db.query(Message)
        
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
    admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get only flagged messages for review"""
    return await get_all_messages(page=page, flagged=True, admin=admin, db=db)


@router.post("/{message_id}/moderate")
async def moderate_message(
    message_id: UUID,
    request: ModerateMessageRequest,
    admin: User = Depends(get_current_admin_user),
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
    admin: User = Depends(get_current_admin_user),
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
        
        # Most active senders
        active_senders = db.query(
            Message.sender_id,
            func.count(Message.id).label('message_count')
        ).group_by(Message.sender_id)\
         .order_by(func.count(Message.id).desc())\
         .limit(10)\
         .all()
        
        return {
            "total_messages": total_messages,
            "messages_today": messages_today,
            "messages_this_week": messages_week,
            "flagged_messages": flagged_count,
            "most_active_senders": [
                {"player_id": str(sender_id), "message_count": count}
                for sender_id, count in active_senders
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))