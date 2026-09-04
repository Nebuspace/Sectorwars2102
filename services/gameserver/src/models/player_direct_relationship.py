"""Player↔player direct relationship + contract posting suspension (LEG-3994).

Canon: sw2102-docs FEATURES/economy/contracts.md § Anti-griefing —
- Hostility = negative direct-relationship reputation (viewer → subject).
- Pairwise blocklist = viewer has blocked subject (accept-time gate).
- Platform posting block = issuer suspended from posting contracts.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class PlayerDirectRelationship(Base):
    """Directed player↔player standing used by contract anti-griefing.

    ``viewer_player_id`` is the party whose opinion is stored;
    ``subject_player_id`` is who that opinion is about. Missing row ⇒
    neutral (reputation 0) and not blocked.
    """

    __tablename__ = "player_direct_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    viewer_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    # contracts.md: "negative direct-relationship reputation" ⇒ hostile
    reputation = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # Accept-time pairwise block: viewer has blocked subject
    is_blocked = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    viewer = relationship("Player", foreign_keys=[viewer_player_id])
    subject = relationship("Player", foreign_keys=[subject_player_id])

    __table_args__ = (
        UniqueConstraint(
            "viewer_player_id",
            "subject_player_id",
            name="uq_player_direct_relationships_viewer_subject",
        ),
    )

    @property
    def is_hostile(self) -> bool:
        return self.reputation < 0


class ContractPostingBlock(Base):
    """Platform-level posting suspension (contracts.md POST: caller not blocklisted).

    Distinct from pairwise ``PlayerDirectRelationship.is_blocked`` (accept /
    board issuer-hide). Presence of a row means the player cannot post.
    """

    __tablename__ = "contract_posting_blocks"

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    player = relationship("Player", foreign_keys=[player_id])
