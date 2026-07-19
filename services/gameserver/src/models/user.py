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
    # Flat ``users.is_admin`` column kept as a denormalized cache (grant/revoke
    # still sync it). Phase C3: Python ``is_admin`` is grant-derived — matches
    # the SQL ``.expression`` EXISTS(active AdminScopeGrant). Physical column
    # is NOT dropped in C3 (dual-read / holders / window proof).
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
        """Derived: any active AdminScopeGrant (C3 flip).

        When the instance is attached to a Session, authoritative read is
        ``EXISTS(revoked_at IS NULL)`` — same predicate as ``.expression``.
        Detached / transient instances fall back to the flat denormalized
        column (still maintained by grant/revoke sync).
        """
        from sqlalchemy.orm import object_session

        session = object_session(self)
        if session is not None and self.id is not None:
            from src.models.admin_scope_grant import AdminScopeGrant

            return (
                session.query(AdminScopeGrant.id)
                .filter(
                    AdminScopeGrant.user_id == self.id,
                    AdminScopeGrant.revoked_at.is_(None),
                )
                .first()
                is not None
            )
        return bool(self._is_admin)

    @is_admin.setter
    def is_admin(self, value: bool) -> None:
        """Write the flat denormalized cache only (grant rows are authoritative)."""
        self._is_admin = bool(value)

    @is_admin.expression
    def is_admin(cls):
        """SQL derived view: any active AdminScopeGrant for this user.

        Correlated EXISTS so ``User.is_admin == True`` keeps working at the
        four SQL filter sites (user_service / auth.admin / auth / test).
        Must match the Python getter for every seeded admin (C3 accept).
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
