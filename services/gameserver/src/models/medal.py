"""
Medal catalog + per-player award models.

ADR-0028 (Medal storage as association table over JSONB): medals use a relational
``Medal`` catalog table plus a ``PlayerMedal`` association table — NOT a
``Player.medals`` JSONB column. The ``UNIQUE (player_id, medal_id)`` constraint on
PlayerMedal is the DB-layer idempotency keystone that defeats the award race; the
``medal_id`` FK uses ``ON DELETE RESTRICT`` so catalog deletions are blocked while
any player still holds the medal.

Explicitly OUT OF SCOPE per ADR-0028: the ``Player.medal_summary`` JSONB cache
(reserved as a future optimization; do NOT build it here).
"""

from uuid import uuid4
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base


class Medal(Base):
    """Medal catalog entry. Stable string PK (e.g. ``combat.bronze_star``)."""

    __tablename__ = "medals"

    # Stable string PK seeded from medal_catalog.py (ADR-0028).
    id = Column(String(100), primary_key=True)

    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    # Grouping (e.g. combat / trade / exploration) and tier (bronze/silver/gold...).
    category = Column(String(50), nullable=False, index=True)
    tier = Column(String(50), nullable=True)
    # Award trigger / unlock conditions (catalog-defined, structured).
    criteria = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    awards = relationship("PlayerMedal", back_populates="medal")

    def __repr__(self) -> str:
        return f"<Medal id={self.id} name='{self.name}' tier={self.tier}>"


class PlayerMedal(Base):
    """A single medal award to a single player. UNIQUE(player_id, medal_id)."""

    __tablename__ = "player_medals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ON DELETE RESTRICT: catalog deletions blocked while any player holds it.
    medal_id = Column(
        String(100),
        ForeignKey("medals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    awarded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Per-award provenance (ADR-0028).
    awarded_via = Column(String(50), nullable=True)  # auto / admin / backfill / bulk
    source_combat_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey("combat_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_event_key = Column(String(255), nullable=True)
    awarded_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    grant_batch_id = Column(UUID(as_uuid=True), nullable=True)
    context_payload = Column(JSONB, nullable=True)
    is_hidden_per_player = Column(JSONB, nullable=True)  # privacy flags

    # Relationships
    medal = relationship("Medal", back_populates="awards")
    player = relationship("Player")

    __table_args__ = (
        # Idempotency keystone: a medal can only be awarded once per player.
        UniqueConstraint("player_id", "medal_id", name="uq_player_medal"),
        # Trophy Room recency.
        Index("ix_player_medals_player_awarded", "player_id", "awarded_at"),
    )

    def __repr__(self) -> str:
        return f"<PlayerMedal player={self.player_id} medal={self.medal_id}>"
