import uuid
import enum
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class GenesisType(enum.Enum):
    STANDARD = "STANDARD"          # Basic terraforming
    ENHANCED = "ENHANCED"          # Improved terraforming
    SPECIALIZED = "SPECIALIZED"    # Specialized for specific planet types
    ADVANCED = "ADVANCED"          # Advanced multi-phase terraforming
    EXPERIMENTAL = "EXPERIMENTAL"  # Unpredictable results
    QUANTUM = "QUANTUM"            # Uses quantum technology


class GenesisStatus(enum.Enum):
    INACTIVE = "INACTIVE"         # Not yet deployed
    DEPLOYING = "DEPLOYING"       # In the process of deployment
    ACTIVE = "ACTIVE"             # Currently active
    COMPLETED = "COMPLETED"       # Successfully completed
    FAILED = "FAILED"             # Failed during process
    UNSTABLE = "UNSTABLE"         # Process is unstable
    ABORTED = "ABORTED"           # Process was manually aborted


class GenesisDevice(Base):
    __tablename__ = "genesis_devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Identification
    name = Column(String(100), nullable=False)
    serial_number = Column(String(50), nullable=False, unique=True)
    
    # Device type and status
    type = Column(Enum(GenesisType, name="genesis_type"), nullable=False)
    status = Column(Enum(GenesisStatus, name="genesis_status"), nullable=False, default=GenesisStatus.INACTIVE)
    
    # Ownership
    owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    creator_faction = Column(String, nullable=True)
    
    # Location and deployment
    ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id"), nullable=True)  # If stored on a ship
    sector_id = Column(Integer, nullable=True)  # If deployed
    planet_id = Column(UUID(as_uuid=True), ForeignKey("planets.id"), nullable=True)  # Target planet
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Genesis capabilities
    terraforming_power = Column(Integer, nullable=False, default=100)  # 1-100 scale
    terraforming_types = Column(ARRAY(String), nullable=False, default=[])  # What planet types it can create
    resource_generation = Column(JSONB, nullable=False, default={})  # Resources it can generate
    special_features = Column(ARRAY(String), nullable=False, default=[])  # Special terraforming abilities
    
    # Genesis process
    phase = Column(Integer, nullable=False, default=0)  # Current phase of the process
    total_phases = Column(Integer, nullable=False, default=1)  # Total phases required
    progress = Column(Float, nullable=False, default=0.0)  # 0.0-1.0 progress
    estimated_completion = Column(DateTime(timezone=True), nullable=True)  # When it will complete
    
    # Operational details
    resource_consumption = Column(JSONB, nullable=False, default={})  # Resources consumed during process
    stability = Column(Float, nullable=False, default=1.0)  # 0.0-1.0, affects success chances
    failure_chance = Column(Float, nullable=False, default=0.0)  # 0.0-1.0 chance of failure
    
    # Security
    security_level = Column(Integer, nullable=False, default=1)  # 1-10 scale
    access_code = Column(String, nullable=True)  # Optional access restriction
    
    # Process results
    result_planet_type = Column(String, nullable=True)  # What type of planet was created
    result_planet_quality = Column(Float, nullable=True)  # 0.0-2.0 quality rating
    result_resources = Column(JSONB, nullable=True)  # Resources created
    result_special_features = Column(ARRAY(String), nullable=True)  # Special features created
    
    # Relationships
    owner = relationship("Player", back_populates="genesis_devices")
    ship = relationship("Ship", back_populates="genesis_device_objects")
    planet = relationship("Planet", foreign_keys="[Planet.genesis_device_id]", back_populates="genesis_device")
    
    def __repr__(self):
        return f"<GenesisDevice {self.name} ({self.type.name}) - Status: {self.status.name}>"


class PlanetFormation(Base):
    __tablename__ = "planet_formations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Genesis device that initiated the formation
    genesis_device_id = Column(UUID(as_uuid=True), ForeignKey("genesis_devices.id"), nullable=False)
    
    # Location information
    sector_id = Column(Integer, nullable=False)
    original_conditions = Column(JSONB, nullable=False, default={})  # Original sector conditions
    
    # Formation process
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    estimated_duration = Column(Integer, nullable=False)  # Hours
    current_phase = Column(Integer, nullable=False, default=1)
    total_phases = Column(Integer, nullable=False)
    
    # Status and results
    is_completed = Column(Boolean, nullable=False, default=False)
    is_failed = Column(Boolean, nullable=False, default=False)
    failure_reason = Column(String, nullable=True)
    resulting_planet_id = Column(UUID(as_uuid=True), ForeignKey("planets.id"), nullable=True)
    
    # Process logs
    formation_log = Column(JSONB, nullable=False, default=[])  # Log of formation events
    anomalies = Column(JSONB, nullable=False, default=[])  # Unexpected events during formation
    
    # Relationships
    genesis_device = relationship("GenesisDevice", foreign_keys=[genesis_device_id], back_populates="formations")
    resulting_planet = relationship("Planet", foreign_keys=[resulting_planet_id], back_populates="formation")
    
    def __repr__(self):
        status = "Completed" if self.is_completed else "Failed" if self.is_failed else "In Progress"
        return f"<PlanetFormation in Sector {self.sector_id} - Status: {status}>"


# Add relationship to GenesisDevice
GenesisDevice.formations = relationship("PlanetFormation", back_populates="genesis_device", cascade="all, delete-orphan")