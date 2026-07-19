"""
Message model for player-to-player and team communication
"""

from sqlalchemy import Column, String, DateTime, Text, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from src.core.database import Base


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Sender and recipient information
    sender_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False, index=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), index=True)  # Null for team messages
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), index=True)  # Null for direct messages
    
    # Message content
    subject = Column(String(255))
    content = Column(Text, nullable=False)
    
    # Message metadata
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = Column(DateTime)
    deleted_by_sender = Column(Boolean, default=False)
    deleted_by_recipient = Column(Boolean, default=False)
    
    # Threading support
    thread_id = Column(UUID(as_uuid=True), index=True)  # For conversation threads
    reply_to_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"))  # For direct replies
    
    # Message type and flags
    message_type = Column(String(20), default="player", nullable=False)  # 'player', 'team', 'system'
    priority = Column(String(10), default="normal")  # 'low', 'normal', 'high', 'urgent'
    
    # Moderation flags
    flagged = Column(Boolean, default=False)
    flagged_reason = Column(String(255))
    moderated_at = Column(DateTime)
    moderated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Audit-trail status: NULL = visible (default). 'deleted' = moderator
    # removed it from player-facing reads but the row (and its moderator
    # stamps above) is kept for the audit log -- see messaging.md
    # "Moderated messages remain in the database ... even after content
    # removal". Vocabulary NO-CANON beyond NULL|'deleted'; 'redacted' /
    # 'blocked' are reserved for the separately-gated MOD-CANON-ACTIONS set.
    moderation_status = Column(String(16))
    
    # Relationships
    sender = relationship("Player", foreign_keys=[sender_id], backref="sent_messages")
    recipient = relationship("Player", foreign_keys=[recipient_id], backref="received_messages")
    team = relationship("Team", foreign_keys=[team_id], back_populates="messages")
    reply_to = relationship("Message", remote_side=[id], backref="replies")
    moderator = relationship("User", foreign_keys=[moderated_by])
    
    # Indexes for efficient querying
    __table_args__ = (
        # Composite indexes for common queries
        Index("ix_messages_recipient_read", "recipient_id", "read_at"),
        Index("ix_messages_team_sent", "team_id", "sent_at"),
        Index("ix_messages_thread", "thread_id", "sent_at"),
        {"schema": None}
    )
    
    def __repr__(self):
        return f"<Message {self.id} from {self.sender_id} to {self.recipient_id or self.team_id}>"
    
    def to_dict(self, include_content=True):
        """Convert to dictionary for API responses"""
        data = {
            "id": str(self.id),
            "sender_id": str(self.sender_id),
            "recipient_id": str(self.recipient_id) if self.recipient_id else None,
            "team_id": str(self.team_id) if self.team_id else None,
            "subject": self.subject,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "message_type": self.message_type,
            "priority": self.priority,
            "thread_id": str(self.thread_id) if self.thread_id else None,
            "reply_to_id": str(self.reply_to_id) if self.reply_to_id else None,
            "flagged": self.flagged,
            "is_read": self.read_at is not None,
            "moderation_status": self.moderation_status
        }
        
        if include_content:
            data["content"] = self.content
        
        # Include sender info if relationship is loaded
        if hasattr(self, 'sender') and self.sender:
            data["sender_name"] = self.sender.nickname
        
        return data
    
    def mark_as_read(self):
        """Mark message as read"""
        if not self.read_at:
            self.read_at = datetime.utcnow()
    
    def is_visible_to(self, player_id: UUID) -> bool:
        """Check if message is visible to a specific player"""
        # Check if player is sender or recipient
        if self.sender_id == player_id and not self.deleted_by_sender:
            return True
        if self.recipient_id == player_id and not self.deleted_by_recipient:
            return True
        
        # Check if player is in the team for team messages
        if self.team_id and hasattr(self, 'team') and self.team:
            return any(member.id == player_id for member in self.team.members)
        
        return False
    
    def soft_delete_for(self, player_id: UUID):
        """Soft delete message for a specific player"""
        if self.sender_id == player_id:
            self.deleted_by_sender = True
        elif self.recipient_id == player_id:
            self.deleted_by_recipient = True