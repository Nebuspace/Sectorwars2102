"""
Message Service for handling player communication
"""

from collections import defaultdict, deque
from typing import Optional, List, Dict, Any, Deque
from datetime import datetime
from time import monotonic
from uuid import UUID, uuid4
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, desc

from src.models.message import Message
from src.models.player import Player
from src.models.team import Team
from src.services.websocket_service import ConnectionManager
from src.services.notification_service import NotificationService

# Global manager instance
manager = ConnectionManager()

logger = logging.getLogger(__name__)

# Anti-spam send rate limit (sliding window).
# NOTE: This window lives in process memory, so it is per-worker and resets on
# restart — adequate for single-process dev. A multi-worker / multi-replica
# deployment must move this to a shared store (e.g. a Redis sorted-set per
# sender keyed on timestamp) for the limit to hold globally.
_SEND_RATE_MAX = 5            # max sends ...
_SEND_RATE_WINDOW = 60.0      # ... per this many seconds, per sender
_send_history: Dict[UUID, Deque[float]] = defaultdict(deque)


class MessageService:
    """Service for managing player messages"""

    @staticmethod
    def check_send_rate_limit(sender_id: UUID) -> None:
        """Enforce a per-sender sliding-window send limit.

        Raises HTTPException(429) with an honest retry hint when the sender has
        already sent _SEND_RATE_MAX messages within the last _SEND_RATE_WINDOW
        seconds. On success it records the current send.

        In-memory / per-process scope (see module note) — fine for dev; promote
        to Redis for multi-worker production.
        """
        now = monotonic()
        window_start = now - _SEND_RATE_WINDOW
        history = _send_history[sender_id]

        # Drop timestamps that have aged out of the window
        while history and history[0] < window_start:
            history.popleft()

        if len(history) >= _SEND_RATE_MAX:
            retry_after = max(1, int(history[0] + _SEND_RATE_WINDOW - now) + 1)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many messages — limit is {_SEND_RATE_MAX} per "
                    f"{int(_SEND_RATE_WINDOW)}s. Try again in {retry_after}s."
                ),
            )

        history.append(now)


    @staticmethod
    async def send_message(
        db: Session,
        sender_id: UUID,
        recipient_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        subject: Optional[str] = None,
        content: str = "",
        priority: str = "normal",
        reply_to_id: Optional[UUID] = None,
        thread_id: Optional[UUID] = None
    ) -> Message:
        """Send a message to a player or team"""
        
        # Validate sender exists
        sender = db.query(Player).filter(Player.id == sender_id).first()
        if not sender:
            raise ValueError("Sender not found")
        
        # Validate recipient or team
        if recipient_id:
            # A player cannot hail themselves — reject cleanly (the route maps
            # ValueError -> 400) rather than creating a useless self-message.
            if recipient_id == sender_id:
                raise ValueError("Cannot send a message to yourself")
            recipient = db.query(Player).filter(Player.id == recipient_id).first()
            if not recipient:
                raise ValueError("Recipient not found")
            message_type = "player"
        elif team_id:
            team = db.query(Team).filter(Team.id == team_id).first()
            if not team:
                raise ValueError("Team not found")
            # Verify sender is a member of the team
            if not any(member.id == sender_id for member in team.members):
                raise ValueError("Sender is not a member of this team")
            message_type = "team"
        else:
            raise ValueError("Either recipient_id or team_id must be provided")
        
        # Handle threading. A reply only inherits the referenced message's
        # thread when the sender actually participated in it (was the original
        # sender or recipient) — otherwise the reply link is silently ignored
        # (treated as a fresh thread) so a guessed/forged reply_to_id can't
        # splice a stranger into someone else's conversation. We never error on
        # a bad reply_to_id; the message still sends as a new thread.
        if reply_to_id:
            original = db.query(Message).filter(Message.id == reply_to_id).first()
            sender_participated = original is not None and sender_id in (
                original.sender_id, original.recipient_id
            )
            if sender_participated:
                thread_id = original.thread_id or original.id
            else:
                # Drop the unauthorized/unknown reply link; start a new thread.
                reply_to_id = None
                if not thread_id:
                    thread_id = uuid4()
        elif not thread_id:
            # New thread
            thread_id = uuid4()
        
        # Create message
        message = Message(
            sender_id=sender_id,
            recipient_id=recipient_id,
            team_id=team_id,
            subject=subject,
            content=content,
            message_type=message_type,
            priority=priority,
            reply_to_id=reply_to_id,
            thread_id=thread_id
        )
        
        db.add(message)
        db.commit()
        db.refresh(message)
        
        # Send WebSocket notification
        await MessageService._send_notification(db, message, sender)
        
        logger.info(f"Message {message.id} sent from {sender_id} to {recipient_id or team_id}")
        
        return message
    
    @staticmethod
    async def _send_notification(db: Session, message: Message, sender: Player):
        """Dispatch the live notification for a new message.

        Priority-driven fan-out is owned by NotificationService (the module the
        messaging canon names for this — see notification_service.py). It maps
        the message's `priority` to a delivery-surface list (inbox / toast /
        push / modal per messaging.md "Priority levels") and routes the WS frame
        through the EXISTING ConnectionManager helper. Delivery failures there
        are swallowed internally so they can never fail an already-committed
        send.
        """
        await NotificationService.notify_new_message(db, message, sender, manager)
    
    @staticmethod
    async def get_inbox(
        db: Session,
        player_id: UUID,
        unread_only: bool = False,
        page: int = 1,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Get player's inbox messages"""
        
        # Base query for messages sent to this player
        query = db.query(Message).filter(
            and_(
                Message.recipient_id == player_id,
                Message.deleted_by_recipient == False
            )
        )
        
        if unread_only:
            query = query.filter(Message.read_at.is_(None))
        
        # Get total count
        total = query.count()
        unread_count = query.filter(Message.read_at.is_(None)).count()
        
        # Get paginated messages with sender info
        offset = (page - 1) * limit
        messages = query.options(joinedload(Message.sender))\
                      .order_by(desc(Message.sent_at))\
                      .limit(limit)\
                      .offset(offset)\
                      .all()
        
        return {
            "messages": [msg.to_dict() for msg in messages],
            "unread_count": unread_count,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
    
    @staticmethod
    async def get_team_messages(
        db: Session,
        player_id: UUID,
        team_id: UUID,
        page: int = 1,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Get team messages for a player"""
        
        # Verify player is in the team
        team = db.query(Team).filter(Team.id == team_id).first()
        if not team or not any(member.id == player_id for member in team.members):
            raise ValueError("Player is not a member of this team")
        
        # Get team messages
        query = db.query(Message).filter(
            and_(
                Message.team_id == team_id,
                or_(
                    Message.deleted_by_sender == False,
                    Message.sender_id != player_id
                )
            )
        )
        
        # Get total count
        total = query.count()
        
        # Get paginated messages with sender info
        offset = (page - 1) * limit
        messages = query.options(joinedload(Message.sender))\
                      .order_by(desc(Message.sent_at))\
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
    
    @staticmethod
    async def mark_as_read(
        db: Session,
        message_id: UUID,
        player_id: UUID
    ) -> bool:
        """Mark a message as read"""
        
        message = db.query(Message).filter(
            and_(
                Message.id == message_id,
                Message.recipient_id == player_id
            )
        ).first()
        
        if not message:
            return False
        
        message.mark_as_read()
        db.commit()
        
        return True
    
    @staticmethod
    async def delete_message(
        db: Session,
        message_id: UUID,
        player_id: UUID
    ) -> bool:
        """Soft delete a message for a player"""
        
        message = db.query(Message).filter(Message.id == message_id).first()
        
        if not message or not message.is_visible_to(player_id):
            return False
        
        message.soft_delete_for(player_id)
        db.commit()
        
        return True
    
    @staticmethod
    async def get_conversations(
        db: Session,
        player_id: UUID,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Get conversation threads for a player"""
        
        # This is a simplified version - in production you'd want a more
        # sophisticated query to get unique conversations
        
        # Get latest message from each thread
        from sqlalchemy import func
        
        # Subquery to get latest message per thread
        latest_messages = db.query(
            Message.thread_id,
            func.max(Message.sent_at).label('latest_sent')
        ).filter(
            and_(
                or_(
                    Message.sender_id == player_id,
                    Message.recipient_id == player_id
                ),
                or_(
                    and_(Message.sender_id == player_id, Message.deleted_by_sender == False),
                    and_(Message.recipient_id == player_id, Message.deleted_by_recipient == False)
                )
            )
        ).group_by(Message.thread_id).subquery()
        
        # Get the actual messages
        query = db.query(Message).join(
            latest_messages,
            and_(
                Message.thread_id == latest_messages.c.thread_id,
                Message.sent_at == latest_messages.c.latest_sent
            )
        )
        
        total = query.count()
        
        # Get paginated conversations
        offset = (page - 1) * limit
        conversations = query.options(
            joinedload(Message.sender),
            joinedload(Message.recipient)
        ).order_by(desc(Message.sent_at))\
         .limit(limit)\
         .offset(offset)\
         .all()
        
        return {
            "conversations": [msg.to_dict() for msg in conversations],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
    
    @staticmethod
    async def flag_message(
        db: Session,
        message_id: UUID,
        reason: str,
        flagged_by: UUID
    ) -> bool:
        """Flag a message for moderation"""
        
        message = db.query(Message).filter(Message.id == message_id).first()
        
        if not message:
            return False
        
        message.flagged = True
        message.flagged_reason = reason

        db.commit()

        # Notify all admin users about the flagged message
        try:
            from src.models.user import User
            admin_users = db.query(User).filter(
                User.is_admin == True,
                User.is_active == True
            ).all()

            flagging_player = db.query(Player).filter(Player.id == flagged_by).first()
            flagged_by_name = flagging_player.username if flagging_player else str(flagged_by)

            for admin_user in admin_users:
                # Send WebSocket notification to admin connections
                admin_notification = {
                    "type": "flagged_message_alert",
                    "message_id": str(message_id),
                    "flagged_by": str(flagged_by),
                    "flagged_by_name": flagged_by_name,
                    "reason": reason,
                    "message_preview": message.content[:200] if message.content else "",
                    "sender_id": str(message.sender_id),
                    "flagged_at": datetime.utcnow().isoformat()
                }
                await manager.send_personal_message(str(admin_user.id), admin_notification)

            logger.warning(
                f"Message {message_id} flagged by {flagged_by} for: {reason}. "
                f"Notified {len(admin_users)} admin(s)."
            )
        except Exception as e:
            logger.error(f"Failed to notify admins about flagged message {message_id}: {e}")
            logger.warning(f"Message {message_id} flagged by {flagged_by} for: {reason}")

        return True
    
    @staticmethod
    async def moderate_message(
        db: Session,
        message_id: UUID,
        action: str,
        moderator_id: UUID,
        reason: Optional[str] = None
    ) -> bool:
        """Moderate a flagged message (admin only)"""
        
        message = db.query(Message).filter(Message.id == message_id).first()
        
        if not message:
            return False
        
        if action == "delete":
            # Hard delete the message
            db.delete(message)
        elif action == "unflag":
            message.flagged = False
            message.flagged_reason = None
        elif action == "flag":
            message.flagged = True
            message.flagged_reason = reason
        else:
            return False
        
        message.moderated_at = datetime.utcnow()
        message.moderated_by = moderator_id
        
        db.commit()
        
        logger.info(f"Message {message_id} moderated by {moderator_id}: {action}")
        
        return True