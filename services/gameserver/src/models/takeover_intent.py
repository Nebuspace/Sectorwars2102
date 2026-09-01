"""Region GC-subscription takeover intent model (LEG-3639 / LEG-286 slice 1).

Canon: sw2102-docs/DATA_MODELS/player.md § TakeoverIntent,
SYSTEMS/region-lifecycle.md § Takeover endpoint, ADR-0050 + ADR-0058 A-F3.

Records a player's intent to take over a Suspended/Grace region while the
PayPal payment flow runs. This WO ships **model + schema only** — no routes,
no PayPal wiring, no expiry sweep (later LEG-286 slices).

State machine (status column):
  pending → won → transferred | failed
  pending → lost | expired

Concurrent claims serialize via SELECT FOR UPDATE on Region + the partial
index on (region_id) WHERE status = 'pending' (ADR-0058 A-F3).

Conventions mirror models/region_invite.py: String enum-in-string (no native PG
enum), UUID PK with Python-side default=uuid.uuid4, TIMESTAMP columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text

from src.core.database import Base


class TakeoverIntentStatus(str, Enum):
    """Lifecycle status of a region GC-subscription takeover intent."""

    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    TRANSFERRED = "transferred"
    FAILED = "failed"
    EXPIRED = "expired"


_TAKEOVER_STATUS_VALUES = tuple(s.value for s in TakeoverIntentStatus)


class TakeoverIntent(Base):
    """PayPal-flow takeover intent for a Suspended/Grace region.

    ``expires_at`` is mandatory (typically created_at + 1 hour — the PayPal
    flow window). ``completed_at`` is set when commit_takeover() runs.
    """

    __tablename__ = "takeover_intents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
    )
    caller_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_url = Column(String, nullable=False)
    status = Column(
        String(20),
        nullable=False,
        default=TakeoverIntentStatus.PENDING,
        server_default=TakeoverIntentStatus.PENDING.value,
    )
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Mandatory TTL — NO server_default; supplied by the takeover service.
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    region = relationship("Region")
    caller = relationship("User")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'won', 'lost', 'transferred', 'failed', 'expired')",
            name="valid_takeover_intent_status",
        ),
        Index("ix_takeover_intents_region_id_status", "region_id", "status"),
        Index("ix_takeover_intents_expires_at", "expires_at"),
        Index(
            "ix_takeover_intents_region_id_pending",
            "region_id",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<TakeoverIntent(region_id='{self.region_id}', "
            f"status='{self.status}', caller_user_id='{self.caller_user_id}')>"
        )

    @property
    def is_expired(self) -> bool:
        """True if the PayPal flow window has passed."""
        if self.expires_at is None:
            return False
        return self.expires_at < datetime.utcnow()
