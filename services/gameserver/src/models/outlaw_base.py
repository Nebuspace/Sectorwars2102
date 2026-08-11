"""OutlawBase — hostile-faction lodging (DATA_MODELS/npc-lodging.md).

Foundation only — discovery/raid surfaces stay deferred
(P10-greenfield-pirate-raid-capture).
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from src.core.database import Base
from src.models.npc_character import NPCArchetype


class OutlawBase(Base):
    __tablename__ = "outlaw_bases"
    __table_args__ = (
        Index("ix_outlaw_bases_region_faction", "home_region_id", "faction_code"),
        Index("ix_outlaw_bases_sector_id", "sector_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    # Global sectors.sector_id (same divergence as NPCCharacter).
    sector_id = Column(Integer, nullable=False)
    home_region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    faction_code = Column(String(50), nullable=False, index=True)
    archetype = Column(Enum(NPCArchetype, name="npc_archetype", create_type=False), nullable=False)
    capacity = Column(Integer, nullable=False)
    current_occupants_count = Column(Integer, nullable=False, default=0)
    assigned_npc_ids = Column(JSONB, nullable=False, default=list)
    is_player_discoverable = Column(Boolean, nullable=False, default=False)
    discovery_requirements = Column(JSONB, nullable=True)
    defenses = Column(JSONB, nullable=False, default=dict)
    amenities = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<OutlawBase {self.name} @sector {self.sector_id}>"
