"""
Zone Model - Security and Policing Regions

Zones represent arbitrary security/policing regions within a parent Region.
They are independent of Clusters (which represent navigation/thematic groupings).

Hierarchy: Sector < Cluster < Zone < Region < Galaxy
           Sector < Zone < Region < Galaxy

Key Characteristics:
- Zones belong to Regions (not Galaxy)
- Zone boundaries are arbitrary (defined by sector_number ranges)
- Zones can split across Cluster boundaries
- A Sector belongs to ONE Zone AND ONE Cluster (orthogonal dimensions)

Zone Types by Region:
- Central Nexus: "The Expanse" (one massive zone, sectors 1-5000)
- Terran Space: Federation/Border/Frontier (three zones in thirds)
- Player Regions: Federation/Border/Frontier (three zones in thirds, DEFAULT)
"""

import uuid
import enum
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, CheckConstraint, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import func

from src.core.database import Base


class ZoneType(str, enum.Enum):
    """Zone type classification based on security and development"""
    EXPANSE = "EXPANSE"          # Central Nexus mega-zone
    FEDERATION = "FEDERATION"    # High security, highly developed
    BORDER = "BORDER"           # Medium security, mixed development
    FRONTIER = "FRONTIER"        # Low security, frontier regions


class Zone(Base):
    """
    Zone - Security/Policing Region within a parent Region

    Zones divide regions into areas with different policing levels and danger ratings.
    They are independent of cluster organization and have arbitrary sector boundaries.
    """
    __tablename__ = "zones"

    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Foreign Keys
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Zone Identity
    name = Column(String(200), nullable=False)  # "The Expanse", "Federation Space", etc.
    zone_type = Column(Enum(ZoneType, name="zone_type"), nullable=False)  # EXPANSE, FEDERATION, BORDER, FRONTIER

    # Sector Boundaries (arbitrary, independent of clusters)
    start_sector = Column(Integer, nullable=False)  # First sector number in this zone (inclusive)
    end_sector = Column(Integer, nullable=False)    # Last sector number in this zone (inclusive)

    # Security Characteristics
    policing_level = Column(
        Integer,
        nullable=False,
        default=5,
        comment="Policing intensity: 0=lawless, 10=maximum security"
    )
    danger_rating = Column(
        Integer,
        nullable=False,
        default=5,
        comment="Danger level: 0=completely safe, 10=extremely dangerous"
    )

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # Relationships
    region = relationship("Region", back_populates="zones")
    sectors = relationship("Sector", back_populates="zone", cascade="all, delete-orphan")

    # Database Constraints
    __table_args__ = (
        # Ensure start_sector is valid (>= 1)
        CheckConstraint('start_sector >= 1', name='check_start_sector_positive'),

        # Ensure end_sector is >= start_sector
        CheckConstraint('end_sector >= start_sector', name='check_end_after_start'),

        # Ensure policing_level is 0-10
        CheckConstraint('policing_level >= 0 AND policing_level <= 10', name='check_policing_range'),

        # Ensure danger_rating is 0-10
        CheckConstraint('danger_rating >= 0 AND danger_rating <= 10', name='check_danger_range'),

        # Unique zone names within a region
        # NOTE: Commented out for flexibility - regions could have zones with same names
        # UniqueConstraint('region_id', 'name', name='unique_zone_name_per_region'),
    )

    def __repr__(self):
        return f"<Zone(id={self.id}, name='{self.name}', type='{self.zone_type}', sectors={self.start_sector}-{self.end_sector})>"

    @property
    def sector_count(self) -> int:
        """Calculate total sectors in this zone's range"""
        return self.end_sector - self.start_sector + 1

    @property
    def is_federation(self) -> bool:
        """Check if this is a Federation zone"""
        return self.zone_type == ZoneType.FEDERATION

    @property
    def is_border(self) -> bool:
        """Check if this is a Border zone"""
        return self.zone_type == ZoneType.BORDER

    @property
    def is_frontier(self) -> bool:
        """Check if this is a Frontier zone"""
        return self.zone_type == ZoneType.FRONTIER

    @property
    def is_expanse(self) -> bool:
        """Check if this is The Expanse (Central Nexus special zone)"""
        return self.zone_type == ZoneType.EXPANSE

    def contains_sector(self, sector_number: int) -> bool:
        """Check if a given sector number falls within this zone's boundaries"""
        return self.start_sector <= sector_number <= self.end_sector
