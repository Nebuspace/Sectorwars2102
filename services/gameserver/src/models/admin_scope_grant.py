"""AdminScopeGrant — per-admin scope grants (RBAC Phase A1, ADR-0058 A-F2).

Canon: sw2102-docs/DATA_MODELS/gameplay.md#AdminScopeGrant.

One row per (user, scope) grant.  A grant is ACTIVE iff ``revoked_at IS NULL``.
``is_active`` is a Python property — the canonical active test is always the
SQL predicate ``revoked_at IS NULL`` (used in indexes + queries), never a
separate boolean column that can drift.

Append-intent: rows are inserted (grant) or updated (revoke fills in
revoked_at/revoked_by).  No ORM cascade deletes rows from this side.

The relationship back to User is one-directional (no back_populates) to avoid
touching user.py in this WO — mirrors the multi_account.py convention.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class AdminScopeGrant(Base):
    """One row per (user, scope) grant.

    Active grants: ``SELECT … WHERE revoked_at IS NULL``
    (also see the partial index ``ix_admin_scope_grants_active``).
    """

    __tablename__ = "admin_scope_grants"
    __table_args__ = (
        # At most one ACTIVE grant per (user, scope).  Revoked rows stay for
        # audit; a re-grant inserts a new row.  Uniqueness is a UNIQUE partial
        # index WHERE revoked_at IS NULL (not a full-table unique on the pair,
        # which would block re-grants after revoke).
        Index(
            "ix_admin_scope_grants_active",
            "user_id",
            "scope",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
        Index("ix_admin_scope_grants_user_id", "user_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Scope string — one of the 19 canonical values in admin_scopes.py.
    scope = Column(String(120), nullable=False)

    granted_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,  # nullable: bootstrap superadmin self-grants on seed
    )
    granted_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # NULL = active; set to revoke
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships — one-directional (no back_populates on User) to avoid
    # touching user.py in this WO.
    user = relationship("User", foreign_keys=[user_id])
    grantor = relationship("User", foreign_keys=[granted_by])
    revoker = relationship("User", foreign_keys=[revoked_by])

    @property
    def is_active(self) -> bool:
        """True iff the grant has not been revoked.

        Use the SQL predicate ``revoked_at IS NULL`` in DB queries; this
        property is for in-process convenience only.
        """
        return self.revoked_at is None

    def __repr__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return f"<AdminScopeGrant user={self.user_id} scope={self.scope!r} {status}>"
