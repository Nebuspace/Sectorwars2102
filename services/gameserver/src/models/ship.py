import uuid
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.genesis_device import GenesisDevice


class ShipType(enum.Enum):
    ESCAPE_POD = "ESCAPE_POD"
    LIGHT_FREIGHTER = "LIGHT_FREIGHTER"
    CARGO_HAULER = "CARGO_HAULER"
    FAST_COURIER = "FAST_COURIER"
    SCOUT_SHIP = "SCOUT_SHIP"
    COLONY_SHIP = "COLONY_SHIP"
    DEFENDER = "DEFENDER"
    CARRIER = "CARRIER"
    WARP_JUMPER = "WARP_JUMPER"
    # NPC-only special-issue police hulls (police-forces.md "NPC-only hull
    # classes"; DATA_MODELS/ships.md ship_type enum). Never serialized to
    # player-facing ShipType lists — the filter lives at the serializer
    # layer (ship_upgrades.py /catalog + /purchase via
    # ShipSpecification.is_npc_only).
    NPC_MARSHAL_INTERDICTOR = "NPC_MARSHAL_INTERDICTOR"
    NPC_SENTINEL_INTERDICTOR = "NPC_SENTINEL_INTERDICTOR"


class FailureType(enum.Enum):
    NONE = "NONE"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CATASTROPHIC = "CATASTROPHIC"


class UpgradeType(enum.Enum):
    ENGINE = "ENGINE"
    CARGO_HOLD = "CARGO_HOLD"
    SHIELD = "SHIELD"
    HULL = "HULL"
    SENSOR = "SENSOR"
    DRONE_BAY = "DRONE_BAY"
    GENESIS_CONTAINMENT = "GENESIS_CONTAINMENT"
    MAINTENANCE_SYSTEM = "MAINTENANCE_SYSTEM"


class InsuranceType(enum.Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"


class ShipStatus(enum.Enum):
    DOCKED = "DOCKED"
    IN_SPACE = "IN_SPACE"
    IN_COMBAT = "IN_COMBAT"
    DESTROYED = "DESTROYED"
    MAINTENANCE = "MAINTENANCE"


class Ship(Base):
    __tablename__ = "ships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(Enum(ShipType, name="ship_type"), nullable=False)
    # NULL owner = NPC-piloted hull (see is_npc + NPCCharacter.ship_id);
    # player ships always carry an owner.
    owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=True)
    # True for NPC-piloted ships. Instance-level companion to canon's
    # ShipSpecification.is_npc_only flag (DATA_MODELS/ships.md): police
    # Interdictors carry both, while v1 pirate hulls reuse player
    # ShipTypes (no canon pirate hull stats exist yet) and rely on this
    # instance flag alone.
    is_npc = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    sector_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Movement
    base_speed = Column(Float, nullable=False)
    current_speed = Column(Float, nullable=False)
    turn_cost = Column(Integer, nullable=False)
    warp_capable = Column(Boolean, nullable=False, default=False)
    
    # Operational status
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(Enum(ShipStatus, name="ship_status"), nullable=False, default=ShipStatus.DOCKED)
    maintenance = Column(JSONB, nullable=False)
    
    # Cargo & special equipment
    cargo = Column(JSONB, nullable=False)
    has_cloaking = Column(Boolean, nullable=False, default=False)
    genesis_devices = Column(Integer, nullable=False, default=0)
    max_genesis_devices = Column(Integer, nullable=False, default=0)
    mines = Column(Integer, nullable=False, default=0)
    max_mines = Column(Integer, nullable=False, default=0)
    has_automated_maintenance = Column(Boolean, nullable=False, default=False)
    
    # Combat
    combat = Column(JSONB, nullable=False)
    attack_turn_cost = Column(Integer, nullable=True)  # Turn cost to initiate combat with this ship

    # Upgrades and modifications
    upgrades = Column(JSONB, nullable=False, default=[])

    # Equipment slots
    equipment_slots = Column(JSONB, nullable=False, default={})

    # Insurance
    insurance = Column(JSONB, nullable=True)
    
    # Special flags
    is_destroyed = Column(Boolean, nullable=False, default=False)
    is_flagship = Column(Boolean, nullable=False, default=False)
    purchase_value = Column(Integer, nullable=False)
    current_value = Column(Integer, nullable=False)

    # Relationships
    owner = relationship("Player", back_populates="ships", foreign_keys=[owner_id])
    flagship_of = relationship("Player", foreign_keys="Player.current_ship_id", post_update=True, overlaps="current_ship")
    sector = relationship("Sector", primaryjoin="Ship.sector_id==Sector.sector_id", foreign_keys=[sector_id])
    
    # New relationships
    genesis_device_objects = relationship("GenesisDevice", back_populates="ship")
    fleet_membership = relationship("FleetMember", back_populates="ship", uselist=False)

    def __repr__(self):
        return f"<Ship {self.name} ({self.type.name}) - Owner: {self.owner_id}>"
        
    @property
    def owner_name(self) -> str:
        """Return the ship owner's name - uses the Player.username property.

        NPC-piloted ships (owner_id NULL, is_npc True) have no Player owner;
        their pilot's display name lives on NPCCharacter (ship_id FK).
        """
        if self.owner:
            return self.owner.username
        return "Unknown"


class ShipSpecification(Base):
    __tablename__ = "ship_specifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(Enum(ShipType, name="ship_type"), nullable=False, unique=True)
    # NPC-only special-issue hull (canon DATA_MODELS/ships.md +
    # police-forces.md "Interdictor hulls"): players can never purchase,
    # capture, salvage, or claim these — ownership-transfer paths reject
    # with ERR_NPC_ONLY_HULL and player-facing catalogs filter them out.
    is_npc_only = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    base_cost = Column(Integer, nullable=False)
    speed = Column(Float, nullable=False)
    turn_cost = Column(Integer, nullable=False)
    max_cargo = Column(Integer, nullable=False)
    max_colonists = Column(Integer, nullable=False)
    max_drones = Column(Integer, nullable=False)
    
    # Defense
    max_shields = Column(Integer, nullable=False)
    shield_recharge_rate = Column(Float, nullable=False)
    hull_points = Column(Integer, nullable=False)
    evasion = Column(Integer, nullable=False)
    
    # Capabilities
    genesis_compatible = Column(Boolean, nullable=False)
    max_genesis_devices = Column(Integer, nullable=False)
    warp_compatible = Column(Boolean, nullable=False)
    warp_creation_capable = Column(Boolean, nullable=False)
    quantum_jump_capable = Column(Boolean, nullable=False)
    scanner_range = Column(Integer, nullable=False)
    
    # Performance
    attack_rating = Column(Integer, nullable=False)
    defense_rating = Column(Integer, nullable=False)
    attack_turn_cost = Column(Integer, nullable=True)  # Turn cost to initiate combat
    maintenance_rate = Column(Float, nullable=False)
    construction_time = Column(Integer, nullable=False)
    fuel_efficiency = Column(Integer, nullable=False)
    
    # Upgrades
    max_upgrade_levels = Column(JSONB, nullable=False)
    
    # Special abilities and metadata
    special_abilities = Column(JSONB, nullable=False, default=[])
    description = Column(String, nullable=False)
    acquisition_methods = Column(JSONB, nullable=False, default=[])
    faction_requirements = Column(JSONB, nullable=True)

    def __repr__(self):
        return f"<ShipSpecification for {self.type.name}>" 