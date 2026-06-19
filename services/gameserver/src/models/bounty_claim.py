"""
Bounty claim provenance model.

Bounties themselves live in ``Player.settings["bounties"]`` JSONB (see
``services/bounty_service.py`` — each entry carries a string ``id``, ``placed_by``,
``amount``). This table records the *claim* of a bounty as durable, queryable
provenance: who claimed which bounty on which target, for how much, when, and the
claim status so cancellation / refund flows have an auditable record outside the
mutable JSONB blob.

``bounty_ref`` is a String (not a FK) because the bounty source is a JSONB entry
id, not a relational row. Player references ARE relational FKs.
"""

import enum
from uuid import uuid4
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class BountyClaimStatus(str, enum.Enum):
    """Lifecycle of a bounty claim."""
    CLAIMED = "claimed"      # claim recorded, payout pending/settled
    PAID = "paid"            # payout disbursed to claimant
    CANCELLED = "cancelled"  # claim voided
    REFUNDED = "refunded"    # amount returned to placer (e.g. invalid claim)


class BountyClaim(Base):
    """Provenance record for a claimed bounty."""

    __tablename__ = "bounty_claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Reference to the bounty JSONB entry id in Player.settings["bounties"].
    bounty_ref = Column(String(100), nullable=False, index=True)

    claimant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    amount = Column(Integer, nullable=False, default=0)
    status = Column(
        SQLEnum(BountyClaimStatus, name="bounty_claim_status", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=BountyClaimStatus.CLAIMED,
    )

    claimed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    claimant = relationship("Player", foreign_keys=[claimant_id])
    target = relationship("Player", foreign_keys=[target_id])

    __table_args__ = (
        Index("ix_bounty_claims_target_status", "target_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<BountyClaim id={self.id} bounty_ref={self.bounty_ref} "
            f"amount={self.amount} status={self.status}>"
        )
