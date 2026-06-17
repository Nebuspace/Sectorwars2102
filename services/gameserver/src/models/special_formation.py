import uuid
import enum
from sqlalchemy import Boolean, Column, DateTime, String, ForeignKey, Enum, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class SpecialFormationType(enum.Enum):
    BUBBLE = "BUBBLE"                     # Multi-sector enclave with single gateway
    DEAD_END_BUBBLE = "DEAD_END_BUBBLE"   # Bubble whose interior sectors all terminate
    GOLD_BUBBLE = "GOLD_BUBBLE"           # Operator-placed bubble, >=100 sectors, may be multi-gateway
    TUNNEL = "TUNNEL"                     # Linear chain of degree-2 sectors between two mouths
    DEAD_END = "DEAD_END"                 # Single terminal sector (degree 1)
    WARP_SINK = "WARP_SINK"               # Inbound warps but zero outbound (TW2002 "Black Hole")
    BACKDOOR = "BACKDOOR"                 # One-way bypass into a bubble or dead-end
    BLISTER = "BLISTER"                   # Side-loop where entry and exit reduce to same sector
    ESCAPE_HATCH = "ESCAPE_HATCH"         # Dead-end with surprise one-way inbound from far away
    # ADR-0070 island formations (bang v1.3.0). Added via Alembic enum extension
    # in the bang_schema_decisions migration; matched here for ORM coverage.
    LOST_SECTOR = "LOST_SECTOR"           # Isolated single-sector island (no warps in/out)
    LOST_CLUSTER = "LOST_CLUSTER"         # Isolated multi-sector island cluster
    ARCHIPELAGO = "ARCHIPELAGO"           # Group of LOST_CLUSTER islands sharing a discovery key


class SpecialFormation(Base):
    """
    A strategically-significant warp-graph topology stamped into a region by the
    galaxy generator. See sw2102-docs/DATA_MODELS/special-formations.md for the
    catalog and sw2102-docs/SYSTEMS/special-formations-generation.md for the
    stamping pipeline.

    Formations are an orthogonal axis to clusters: a Bubble can sit inside a
    Trade Hub cluster; a Tunnel can cross a Nebula cluster's boundary. A cluster
    says "what biome", a formation says "what graph shape".
    """
    __tablename__ = "special_formations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(Enum(SpecialFormationType, name="special_formation_type"), nullable=False)

    # ADR-0044: the formation's discoverable identity (e.g. "Bubble of the Lost
    # Star"). Previously lived in the properties JSONB; promoted to a first-class
    # column with a per-region UNIQUE constraint. AI-generated at bang time,
    # non-null after generation.
    name = Column(String(100), nullable=True)

    # The topologically distinguished sector for the formation: gateway for
    # BUBBLE/DEAD_END_BUBBLE/GOLD_BUBBLE, mouth for TUNNEL, the terminal sector
    # for DEAD_END/WARP_SINK/ESCAPE_HATCH, the entry sector for BACKDOOR, the
    # pivot for BLISTER. See DATA_MODELS/special-formations.md for the full table.
    anchor_sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Sector UUIDs composing the formation's interior. Empty for single-sector
    # formations (DEAD_END, WARP_SINK, ESCAPE_HATCH). Note: Postgres cannot enforce
    # FK constraints on array elements, so application code must validate that
    # every UUID resolves to a Sector in the same region.
    interior_sector_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)

    # Type-specific parameters. Schema in DATA_MODELS/jsonb-schema.md.
    # Common keys by type:
    #   BUBBLE/DEAD_END_BUBBLE: link_tunnel_depth, branching
    #   GOLD_BUBBLE:            gateway_count, interior_size_min
    #   TUNNEL:                 length, one_way_bias
    #   DEAD_END:               parent_kind, is_unfigged_only
    #   WARP_SINK:              entry_count, recovery_method
    #   BACKDOOR:               target_formation_id, entry_distance
    #   BLISTER:                interior_size, bypass_distance
    #   ESCAPE_HATCH:           surprise_source_distance
    properties = Column(JSONB, nullable=False, default=dict)

    # Discovery state. Default false; flipped when a player first observes the
    # topology (e.g., probes the gateway or scans an interior sector).
    is_discovered = Column(Boolean, nullable=False, default=False)
    discovery_requirement = Column(JSONB, nullable=True)

    # Seed used to stamp this formation, recorded for reproducibility and audit.
    generation_seed = Column(String, nullable=True)

    # Bidirectional relationships. Region.formations and Sector.anchored_formations
    # are the reverse collections. Note: reverse lookup of "formations whose interior
    # contains sector X" is not a SQLAlchemy relationship (it queries the ARRAY column
    # via GIN containment): SpecialFormation.query.filter(
    #     SpecialFormation.interior_sector_ids.contains([sector.id])
    # ).
    region = relationship("Region", back_populates="formations")
    anchor_sector = relationship("Sector", back_populates="anchored_formations", foreign_keys=[anchor_sector_id])

    __table_args__ = (
        Index("ix_special_formations_region_type", "region_id", "type"),
        Index("ix_special_formations_interior_sector_ids", "interior_sector_ids", postgresql_using="gin"),
        # ADR-0044: formation names are unique within a region.
        UniqueConstraint("region_id", "name", name="uq_special_formations_region_name"),
    )

    def __repr__(self):
        return f"<SpecialFormation {self.type.name} anchor={self.anchor_sector_id} region={self.region_id}>"
