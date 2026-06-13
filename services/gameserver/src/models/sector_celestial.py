"""Persisted per-sector celestial composition + per-sector feature discovery.

ADR-0073: a sector's "solar system" (star kind, body skeleton, asteroid belt,
nebula, collision-debris ring, habitable zone) was regenerated procedurally on
every request and never stored. Persisting it (generate-once-then-stable) is the
prerequisite for discoverer-named worlds that survive a reload, future evolving
sector state, and querying the galaxy by composition.

Storage decision (ADR-0073, our call): a DEDICATED table, not JSONB on the hot,
heavily-FOR-UPDATE-locked ``sectors`` row. Composition is read by exactly one
endpoint and gets its own timestamps; the sectors hot path stays lean.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Integer, BigInteger, ForeignKey, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB

from src.core.database import Base


class SectorCelestial(Base):
    """One row per sector: the stable procedural SKELETON of its system (star,
    extra stars, nebula, belt, collision-debris ring, body slots, habitable
    zone). Generated once on first visit, then read-through + mutable. Real
    Planet/Station rows are merged over this skeleton at read time."""

    __tablename__ = "sector_celestials"

    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), primary_key=True)
    # Human-readable sector number, for convenience lookup matching Sector.sector_id.
    sector_id = Column(Integer, nullable=False, index=True)
    # The deterministic procedural skeleton (star/extra_stars/nebula/belt/debris/
    # bodies/habitable_zone). Real planets are NOT stored here — merged at read.
    composition = Column(JSONB, nullable=False)
    seed = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<SectorCelestial sector={self.sector_id}>"


class SectorFeatureDiscovery(Base):
    """Per-sector hidden-feature discovery, kept SEPARATE from planet discovery
    and from the sector's first-discoverer (Sector.discovered_by_id) so future
    hidden per-sector features can be discovered independently (ADR-0073).
    Each (sector, feature_type) is discovered once; the discoverer is recorded."""

    __tablename__ = "sector_feature_discoveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True)
    # e.g. "belt", "debris", "nebula", or future "hidden_anomaly".
    feature_type = Column(String(40), nullable=False)
    discovered_by = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    discovered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("sector_uuid", "feature_type", name="uq_sector_feature"),
    )

    def __repr__(self) -> str:
        return f"<SectorFeatureDiscovery sector={self.sector_uuid} feature={self.feature_type}>"
