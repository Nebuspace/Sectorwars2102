from datetime import datetime, UTC
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, index=True, 
                server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    revoked = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self):
        status = "Active" if not self.revoked else "Revoked"
        return f"<RefreshToken id={self.id}, user_id={self.user_id}, revoked={self.revoked} ({status})>"
    
    @property
    def is_expired(self) -> bool:
        """Check if this token is expired."""
        return datetime.now(UTC) > self.expires_at
    
    @property
    def is_valid(self):
        return not self.revoked and not self.is_expired