import uuid
import enum
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func, Table
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


# Association table for sector warps (connections between sectors)
sector_warps = Table(
    'sector_warps',
    Base.metadata,
    Column('source_sector_id', UUID(as_uuid=True), ForeignKey('sectors.id', ondelete="CASCADE"), primary_key=True),
    Column('destination_sector_id', UUID(as_uuid=True), ForeignKey('sectors.id', ondelete="CASCADE"), primary_key=True),
    Column('is_bidirectional', Boolean, default=True, nullable=False),
    Column('turn_cost', Integer, default=1, nullable=False),
    Column('warp_stability', Float, default=1.0, nullable=False),  # 0.0-1.0, affects reliability
    Column('created_at', DateTime(timezone=True), server_default=func.now(), nullable=False),
)


# Enum for sector special types - aligned with data definition
class SectorSpecialType(enum.Enum):
    NORMAL = "NORMAL"                    # Standard sector
    NEBULA = "NEBULA"                    # Affects sensors and combat
    ASTEROID_FIELD = "ASTEROID_FIELD"    # Resource-rich, affects movement
    BLACK_HOLE = "BLACK_HOLE"            # Gravitational effects, danger
    RADIATION_ZONE = "RADIATION_ZONE"    # Damages ships over time
    WARP_STORM = "WARP_STORM"            # Disrupts warp tunnels, temporary

# Keep legacy SectorType for backward compatibility
class SectorType(enum.Enum):
    STANDARD = "STANDARD"
    NEBULA = "NEBULA"  # Resource rich, movement difficult
    ASTEROID_FIELD = "ASTEROID_FIELD"  # Mining opportunities
    BLACK_HOLE = "BLACK_HOLE"  # Special movement rules
    STAR_CLUSTER = "STAR_CLUSTER"  # Multiple stars, planets
    VOID = "VOID"  # Empty, fast travel
    INDUSTRIAL = "INDUSTRIAL"  # Manufactured goods
    AGRICULTURAL = "AGRICULTURAL"  # Food production
    FORBIDDEN = "FORBIDDEN"  # Restricted access
    WORMHOLE = "WORMHOLE"  # Special warping mechanics


class Sector(Base):
    __tablename__ = "sectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sector_id = Column(Integer, nullable=False, unique=True)  # Human-readable sector number
    sector_number = Column(Integer, nullable=True)  # New field for Central Nexus (can be same as sector_id)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Multi-regional fields
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=True)
    security_level = Column(Integer, nullable=True, default=5)  # 1-10 scale
    development_level = Column(Integer, nullable=True, default=1)  # 1-10 scale
    traffic_level = Column(Integer, nullable=True, default=1)  # 1-10 scale
    
    # Relationships and structure
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(Enum(SectorType, name="sector_type"), nullable=False, default=SectorType.STANDARD)
    
    # Discovery status
    is_discovered = Column(Boolean, nullable=False, default=True)
    discovered_by_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=True)
    discovery_date = Column(DateTime(timezone=True), nullable=True)
    
    # Physical properties
    x_coord = Column(Integer, nullable=False)  # Grid coordinates for mapping
    y_coord = Column(Integer, nullable=False)
    z_coord = Column(Integer, nullable=False, default=0)  # For potential 3D mapping
    radiation_level = Column(Float, nullable=False, default=0.0)  # Affects ship maintenance
    hazard_level = Column(Integer, nullable=False, default=0)  # 0-10 scale
    
    # Resources - aligned with data definition
    resources = Column(JSONB, nullable=False, default={
        "has_asteroids": False,
        "asteroid_yield": {
            "ore": 0,
            "precious_metals": 0,
            "radioactives": 0
        },
        "gas_clouds": [],
        "has_scanned": False
    })
    resource_regeneration = Column(Float, nullable=False, default=1.0)  # Rate multiplier
    
    # Occupancy and control - aligned with data definition
    players_present = Column(JSONB, nullable=False, default=[])  # List of player IDs currently in sector
    ships_present = Column(JSONB, nullable=False, default=[])  # Ships currently in sector
    
    # Defenses - aligned with data definition
    defenses = Column(JSONB, nullable=False, default={
        "defense_drones": 0,
        "owner_id": None,
        "owner_name": None,
        "team_id": None,
        "mines": 0,
        "mine_owner_id": None,
        "patrol_ships": []
    })
    
    controlling_faction = Column(String, nullable=True)  # Null means uncontrolled or contested
    controlling_team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    last_combat = Column(DateTime(timezone=True), nullable=True)
    
    # Events and special features
    active_events = Column(JSONB, nullable=False, default=[])  # Current sector events
    special_features = Column(ARRAY(String), nullable=False, default=[])
    description = Column(String, nullable=True)
    
    # Navigation properties
    nav_hazards = Column(JSONB, nullable=False, default={})  # Navigation hazards
    nav_beacons = Column(JSONB, nullable=False, default=[])  # Navigation markers
    
    # Relationships
    cluster = relationship("Cluster", back_populates="sectors")
    zone = relationship("Zone", back_populates="sectors")
    region = relationship("Region", back_populates="sectors")
    planets = relationship("Planet", back_populates="sector", cascade="all, delete-orphan")
    stations = relationship("Station", back_populates="sector", cascade="all, delete-orphan")
    ships = relationship("Ship", primaryjoin="Sector.sector_id==Ship.sector_id", foreign_keys="Ship.sector_id", overlaps="sector")
    discovered_by = relationship("Player", back_populates="discovered_sectors")
    controlling_team = relationship("Team", back_populates="controlled_sectors")
    deployed_drones = relationship("Drone", back_populates="sector")
    drone_deployments = relationship("DroneDeployment", back_populates="sector")
    fleets = relationship("Fleet", back_populates="sector")
    anchored_formations = relationship("SpecialFormation", back_populates="anchor_sector", foreign_keys="SpecialFormation.anchor_sector_id")
    
    # Warp connections
    outgoing_warps = relationship(
        "Sector",
        secondary=sector_warps,
        primaryjoin=id==sector_warps.c.source_sector_id,
        secondaryjoin=id==sector_warps.c.destination_sector_id,
        backref="incoming_warps"
    )
    
    warp_tunnels_origin = relationship(
        "WarpTunnel", 
        back_populates="origin_sector",
        foreign_keys="WarpTunnel.origin_sector_id",
        cascade="all, delete-orphan"
    )
    
    warp_tunnels_destination = relationship(
        "WarpTunnel", 
        back_populates="destination_sector",
        foreign_keys="WarpTunnel.destination_sector_id",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<Sector {self.sector_id}: {self.name} ({self.type.name})>"