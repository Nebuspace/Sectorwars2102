"""AdminActionLog — append-only admin action audit trail (RBAC Phase A1, ADR-0058 A-F2).

Canon: sw2102-docs/DATA_MODELS/gameplay.md#AdminActionLog.

APPEND-ONLY intent: rows are inserted after each admin action.  No ORM helper
deletes rows; the FK from admin_user_id → users uses SET NULL (not CASCADE) so
deleting a user account does not wipe their audit trail.

reviewed_by / reviewed_at: filled by the Phase E review queue when an ack
holder marks the action reviewed.  NULL = not yet reviewed.

The relationship back to User is one-directional (no back_populates on User)
to avoid touching user.py in this WO — mirrors the multi_account.py convention.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class AdminActionLog(Base):
    """One row per admin action.  Append-only — no update/delete helpers."""

    __tablename__ = "admin_action_logs"
    __table_args__ = (
        Index("ix_admin_action_logs_admin_user_id", "admin_user_id"),
        Index("ix_admin_action_logs_at", "at"),
        # Fast lookup for the Phase E review queue: unreviewed high-impact rows
        Index(
            "ix_admin_action_logs_scope_reviewed",
            "scope_used",
            "reviewed_at",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # SET NULL — deleting a user does NOT cascade-delete their audit log.
    admin_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The scope the admin was operating under when this action was taken.
    scope_used = Column(String(120), nullable=True)

    # Human-readable action label, e.g. "suspend_player", "replay_webhook".
    action = Column(String(200), nullable=False)

    # The kind of object affected, e.g. "player", "region", "webhook".
    target_type = Column(String(100), nullable=True)

    # The ID of the affected object (stored as text to accommodate any PK type).
    target_id = Column(String(255), nullable=True)

    # Sanitized snapshot of the request payload at action time.
    payload_snapshot = Column(JSONB, nullable=True)

    # "success" | "failure" | any other outcome label
    result = Column(String(50), nullable=True)

    # Set on failure — what went wrong.
    failure_reason = Column(Text, nullable=True)

    # Phase E: reviewer fills these in via the review queue.
    reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Immutable action timestamp.
    at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships — one-directional (no back_populates on User).
    admin_user = relationship("User", foreign_keys=[admin_user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return (
            f"<AdminActionLog id={self.id} admin={self.admin_user_id} "
            f"action={self.action!r} scope={self.scope_used!r}>"
        )
