"""NPCBarracks — lawful-faction lodging (DATA_MODELS/npc-lodging.md).

Foundation slice (WO-BUILD-NPC-LODGING-FOUNDATION): schema + occupancy
helpers. Raid/capture on OutlawBase stays out of scope.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
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


class NPCLodgingLocationType(str, enum.Enum):
    STATION = "station"
    SECTOR = "sector"


class NPCBarracks(Base):
    __tablename__ = "npc_barracks"
    __table_args__ = (
        Index(
            "ix_npc_barracks_region_faction_archetype",
            "home_region_id",
            "faction_code",
            "archetype",
        ),
        Index("ix_npc_barracks_station_id", "station_id"),
        Index("ix_npc_barracks_sector_id", "sector_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    location_type = Column(
        Enum(NPCLodgingLocationType, name="npc_lodging_location"),
        nullable=False,
    )
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Global sectors.sector_id when location_type=sector (same divergence
    # as NPCCharacter.current_sector_id).
    sector_id = Column(Integer, nullable=True)
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
    amenities = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<NPCBarracks {self.name} ({self.faction_code}/{self.archetype.value})>"
