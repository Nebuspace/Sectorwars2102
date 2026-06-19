"""
Per-(sector, faction) influence model.

ADR-0021 (Territory Taxonomy and Influence Math) promotes the previously
"phantom" SectorFactionInfluence entity to a concrete relational table. It is the
single canonical input for the four-tier territory taxonomy (Core 100% /
Controlled >=75% / Contested 40-60% / Uncontrolled 0%), patrol-versus-pirate spawn
weighting, and faction-driven port pricing (ADR-0032/0033 emergent reputation
relies on per-sector faction detection).

Relationship to existing modelling:
  - ``cluster.faction_influence`` is a coarse JSONB summary at the *cluster* grain
    keyed by faction slug (e.g. ``terran_federation``). It stays as-is.
  - ``sector.controlling_faction`` is a denormalized String label (faction name /
    slug, nullable when contested). It stays as-is.

This table is the fine-grained, normalized, per-(sector, faction) source of truth
that drives the dynamic-influence loop without overloading either JSONB blob. It
keys on the real ``factions.id`` UUID (consistent with ``Reputation`` and the rest
of the faction system) and the ``sectors.id`` UUID PK.
"""

from uuid import uuid4
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class SectorFactionInfluence(Base):
    """A single faction's influence over a single sector (ADR-0021)."""

    __tablename__ = "sector_faction_influence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    sector_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    faction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("factions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 0-100 influence percentage (the taxonomy thresholds key off this value).
    influence_percentage = Column(Float, nullable=False, default=0.0)
    # Patrol-versus-pirate spawn weight derived from influence (ADR-0021 effect).
    patrol_spawn_weight = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    sector = relationship("Sector")
    faction = relationship("Faction")

    __table_args__ = (
        UniqueConstraint("sector_id", "faction_id", name="uq_sector_faction_influence"),
    )

    def __repr__(self) -> str:
        return (
            f"<SectorFactionInfluence sector={self.sector_id} "
            f"faction={self.faction_id} influence={self.influence_percentage}>"
        )
