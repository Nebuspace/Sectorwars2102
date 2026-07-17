import uuid
from datetime import datetime
from typing import List, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, func, TIMESTAMP, exists, and_
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from src.core.database import Base


if TYPE_CHECKING:
    from src.models.oauth_account import OAuthAccount
    from src.models.refresh_token import RefreshToken
    from src.models.admin_credentials import AdminCredentials
    from src.models.player_credentials import PlayerCredentials
    from src.models.player import Player
    from src.models.mfa import MFASecret, MFAAttempt


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # Flat column remains authoritative through Phases B/C (GATE OUTCOME).
    # Exposed as hybrid ``is_admin``: Python reads/writes this column; SQL
    # ``User.is_admin`` uses EXISTS(active AdminScopeGrant) for dual-read
    # validation against the eventual derived flip.
    _is_admin = Column("is_admin", Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    deleted = Column(Boolean, default=False, nullable=False)
    
    # PayPal subscription tracking
    paypal_subscription_id = Column(String(255), nullable=True)
    subscription_tier = Column(String(50), nullable=True)  # galactic_citizen, etc.
    subscription_status = Column(String(50), nullable=True)  # active, suspended, cancelled
    subscription_started_at = Column(TIMESTAMP, nullable=True)
    subscription_expires_at = Column(TIMESTAMP, nullable=True)

    # Relationships
    oauth_accounts = relationship("OAuthAccount", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    admin_credentials = relationship("AdminCredentials", back_populates="user", cascade="all, delete-orphan", uselist=False)
    player_credentials = relationship("PlayerCredentials", back_populates="user", cascade="all, delete-orphan", uselist=False)
    player = relationship("Player", back_populates="user", cascade="all, delete-orphan", uselist=False)
    mfa_secret = relationship("MFASecret", back_populates="user", cascade="all, delete-orphan", uselist=False)
    mfa_attempts = relationship("MFAAttempt", back_populates="user", cascade="all, delete-orphan")
    owned_regions = relationship("Region", back_populates="owner")

    @hybrid_property
    def is_admin(self) -> bool:
        """Flat ``users.is_admin`` — authoritative until the post-B derived flip."""
        return bool(self._is_admin)

    @is_admin.setter
    def is_admin(self, value: bool) -> None:
        self._is_admin = bool(value)

    @is_admin.expression
    def is_admin(cls):
        """SQL derived view: any active AdminScopeGrant for this user.

        Correlated EXISTS so ``User.is_admin == True`` keeps working at the
        four SQL filter sites (user_service / auth.admin / auth / test).
        Must match the flat column for every seeded admin (A2 accept).
        """
        # Local import avoids circular import at model-load time.
        from src.models.admin_scope_grant import AdminScopeGrant

        return exists().where(
            and_(
                AdminScopeGrant.user_id == cls.id,
                AdminScopeGrant.revoked_at.is_(None),
            )
        )

    def __repr__(self):
        return f"<User {self.username}>"
