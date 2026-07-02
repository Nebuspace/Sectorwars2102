import uuid
import enum
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class ClusterType(enum.Enum):
    STANDARD = "STANDARD"                         # Balanced mix of sectors
    RESOURCE_RICH = "RESOURCE_RICH"               # High resource concentration
    POPULATION_CENTER = "POPULATION_CENTER"       # Many habitable planets
    TRADE_HUB = "TRADE_HUB"                      # Many ports, economic activity
    MILITARY_ZONE = "MILITARY_ZONE"               # Heavy faction presence, secure
    FRONTIER_OUTPOST = "FRONTIER_OUTPOST"         # Edge of explored space
    CONTESTED = "CONTESTED"                       # Multiple factions competing
    SPECIAL_INTEREST = "SPECIAL_INTEREST"         # Unique features, anomalies


class Cluster(Base):
    __tablename__ = "clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships and structure
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    type = Column(Enum(ClusterType, name="cluster_type"), nullable=False)
    sector_count = Column(Integer, nullable=False, default=0)
    
    # Discovery status
    is_discovered = Column(Boolean, nullable=False, default=True)  # Some clusters start hidden
    discovery_requirement = Column(JSONB, nullable=True)  # Requirements to discover this cluster
    
    # Statistics and Status - aligned with data definition
    stats = Column(JSONB, nullable=False, default={
        "total_sectors": 0,
        "populated_sectors": 0,
        "empty_sectors": 0,
        "resource_value": 50,      # 0-100 overall resource abundance
        "danger_level": 30,        # 0-100 overall threat assessment
        "development_index": 40,   # 0-100 infrastructure level
        "exploration_percentage": 75  # 0-100 how much has been discovered
    })
    
    # Economic and resource properties - aligned with data definition
    resource_modifiers = Column(JSONB, nullable=False, default={})  # Adjustments to resource generation
    economic_focus = Column(ARRAY(String), nullable=False, default=[])  # Primary economic activities
    resources = Column(JSONB, nullable=False, default={
        "primary_resources": [],
        "resource_distribution": {  # canon slugs only — ghost slugs medical_supplies/
            # technology removed WO-ARCH-RES-2I-A; freed 20 points redistributed
            # proportionally over the surviving 4, sum stays 70 (this file's convention)
            "ore": 28,
            "organics": 21,
            "equipment": 14,
            "luxury_goods": 7
        },
        "special_resources": []
    })
    economic_value = Column(Integer, nullable=False, default=50)  # 0-100 economic importance
    
    # Faction influence - aligned with data definition
    controlling_faction = Column(String, nullable=True)  # Null means contested
    faction_influence = Column(JSONB, nullable=False, default={
        "terran_federation": 30,
        "mercantile_guild": 15,
        "frontier_coalition": 20,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 10,
        "dominant_faction": "terran_federation"
    })
    
    # Navigation and coordinates - aligned with data definition
    nav_hazards = Column(ARRAY(String), nullable=False, default=[])
    recommended_ship_class = Column(String, nullable=False, default="light_freighter")
    x_coord = Column(Integer, nullable=False, default=0)
    y_coord = Column(Integer, nullable=False, default=0)
    z_coord = Column(Integer, nullable=False, default=0)
    
    # Special features - aligned with data definition
    special_features = Column(ARRAY(String), nullable=False, default=[])
    description = Column(String, nullable=True)
    is_hidden = Column(Boolean, nullable=False, default=False)

    # Structured nebula fields (WO-DBB-QR4). The bang payload carries nebula
    # data PER-SECTOR only (sector.nebula = {type, density}); there is no
    # cluster-level nebula block. These columns capture the cluster's
    # *dominant* (representative) nebula, derived at import time from its member
    # sectors: nebula_type = the most common nebula type among the cluster's
    # nebula sectors; quantum_field_strength = the mean density of those
    # sectors (the only quantitative nebula attribute the payload provides).
    # color_hex has no source in the bang payload — it is left NULL until a
    # source for it exists. All three are additive + nullable: a cluster with
    # no nebula sectors leaves all three NULL.
    nebula_type = Column(String(50), nullable=True)
    quantum_field_strength = Column(Float, nullable=True)
    color_hex = Column(String(20), nullable=True)
    
    # Warp stability
    warp_stability = Column(Float, nullable=False, default=1.0)  # Affects warp tunnel reliability
    
    # Relationships
    region = relationship("Region", back_populates="clusters")
    sectors = relationship("Sector", back_populates="cluster", cascade="all, delete-orphan")

    __table_args__ = (
        # ADR-0044: cluster names are unique within a region.
        UniqueConstraint("region_id", "name", name="uq_clusters_region_name"),
    )

    def __repr__(self):
        return f"<Cluster {self.name} ({self.type.name}) - {self.sector_count} sectors>"