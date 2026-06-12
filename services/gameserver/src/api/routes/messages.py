"""
Message API endpoints for player communication
"""

import logging
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.message_service import MessageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])


class MessageCreateRequest(BaseModel):
    recipient_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    subject: Optional[str] = Field(None, max_length=255)
    content: str = Field(..., min_length=1, max_length=5000)
    priority: str = Field("normal", pattern="^(low|normal|high|urgent)$")
    reply_to_id: Optional[UUID] = None


class MessageResponse(BaseModel):
    message_id: str
    sent_at: str


@router.post("/send", response_model=MessageResponse)
async def send_message(
    request: MessageCreateRequest,
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Send a message to another player or team"""
    try:
        # Basic validation
        if not request.recipient_id and not request.team_id:
            raise HTTPException(
                status_code=400,
                detail="Either recipient_id or team_id must be provided"
            )

        if request.recipient_id and request.team_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot send to both recipient and team"
            )

        # Anti-spam: per-sender sliding window. Raises HTTPException(429)
        # with an honest retry detail when the sender is over the limit.
        MessageService.check_send_rate_limit(current_player.id)

        # Send the message
        message = await MessageService.send_message(
            db=db,
            sender_id=current_player.id,
            recipient_id=request.recipient_id,
            team_id=request.team_id,
            subject=request.subject,
            content=request.content,
            priority=request.priority,
            reply_to_id=request.reply_to_id
        )

        return MessageResponse(
            message_id=str(message.id),
            sent_at=message.sent_at.isoformat() if message.sent_at else ""
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to send message")
        raise HTTPException(status_code=500, detail="Failed to send message")


@router.get("/inbox")
async def get_inbox(
    page: int = Query(1, ge=1),
    unread_only: bool = Query(False),
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get player's inbox messages"""
    try:
        result = await MessageService.get_inbox(
            db=db,
            player_id=current_player.id,
            unread_only=unread_only,
            page=page,
            limit=50
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to load inbox")
        raise HTTPException(status_code=500, detail="Failed to load inbox")


@router.get("/team/{team_id}")
async def get_team_messages(
    team_id: UUID,
    page: int = Query(1, ge=1),
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get messages for a specific team"""
    try:
        result = await MessageService.get_team_messages(
            db=db,
            player_id=current_player.id,
            team_id=team_id,
            page=page,
            limit=50
        )

        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Failed to load team messages")
        raise HTTPException(status_code=500, detail="Failed to load team messages")


@router.put("/{message_id}/read")
async def mark_message_read(
    message_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Mark a message as read"""
    try:
        success = await MessageService.mark_as_read(
            db=db,
            message_id=message_id,
            player_id=current_player.id
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Message not found")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to mark message read")
        raise HTTPException(status_code=500, detail="Failed to mark message as read")


@router.delete("/{message_id}")
async def delete_message(
    message_id: UUID,
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Delete a message (soft delete)"""
    try:
        success = await MessageService.delete_message(
            db=db,
            message_id=message_id,
            player_id=current_player.id
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Message not found")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete message")
        raise HTTPException(status_code=500, detail="Failed to delete message")


@router.get("/conversations")
async def get_conversations(
    page: int = Query(1, ge=1),
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Get conversation threads"""
    try:
        result = await MessageService.get_conversations(
            db=db,
            player_id=current_player.id,
            page=page,
            limit=20
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to load conversations")
        raise HTTPException(status_code=500, detail="Failed to load conversations")


@router.post("/{message_id}/flag")
async def flag_message(
    message_id: UUID,
    reason: str = Query(..., min_length=10, max_length=255),
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db)
):
    """Flag a message for moderation"""
    try:
        success = await MessageService.flag_message(
            db=db,
            message_id=message_id,
            reason=reason,
            flagged_by=current_player.id
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Message not found")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to flag message")
        raise HTTPException(status_code=500, detail="Failed to flag message")