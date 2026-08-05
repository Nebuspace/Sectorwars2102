from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class PlayerCredentials(Base):
    __tablename__ = "player_credentials"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash = Column(String(255), nullable=False)
    last_password_change = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="player_credentials")

    def __repr__(self):
        return f"<PlayerCredentials for user_id={self.user_id}>"
