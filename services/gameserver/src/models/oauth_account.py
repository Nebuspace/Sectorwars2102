import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(20), nullable=False)  # 'github', 'google', 'steam'
    provider_user_id = Column(String(255), nullable=False)
    provider_account_email = Column(String(255), nullable=True)
    provider_account_username = Column(String(255), nullable=True)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted = Column(Boolean, default=False, nullable=False)

    # Relationships
    user = relationship("User", back_populates="oauth_accounts")

    # Constraints
    __table_args__ = (
        # Unique constraint for provider + provider_user_id combination
        UniqueConstraint("provider", "provider_user_id"),
    )

    def __repr__(self):
        return f"<OAuthAccount {self.provider}:{self.provider_user_id}>"