import uuid
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, BigInteger, Float, ForeignKey, Enum, Table, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.sector import Sector
    from src.models.genesis_device import GenesisDevice, PlanetFormation


# Association table for player-planet relationship
player_planets = Table(
    "player_planets",
    Base.metadata,
    Column("player_id", UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
    Column("planet_id", UUID(as_uuid=True), ForeignKey("planets.id", ondelete="CASCADE"), primary_key=True),
    Column("acquired_at", DateTime(timezone=True), server_default=func.now(), nullable=False)
)


class PlanetType(enum.Enum):
    TERRAN = "TERRAN"
    DESERT = "DESERT"
    OCEANIC = "OCEANIC"
    ICE = "ICE"
    VOLCANIC = "VOLCANIC"
    GAS_GIANT = "GAS_GIANT"
    BARREN = "BARREN"
    JUNGLE = "JUNGLE"
    ARCTIC = "ARCTIC"
    TROPICAL = "TROPICAL"
    MOUNTAINOUS = "MOUNTAINOUS"
    ARTIFICIAL = "ARTIFICIAL"


class PlanetStatus(enum.Enum):
    UNINHABITABLE = "UNINHABITABLE"
    HABITABLE = "HABITABLE"
    COLONIZED = "COLONIZED"
    DEVELOPED = "DEVELOPED"
    TERRAFORMING = "TERRAFORMING"
    DYING = "DYING"
    RESTRICTED = "RESTRICTED"


class Planet(Base):
    __tablename__ = "planets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    # ADR-0073 No-Man's-Sky naming: auto_name is the generated default; the
    # discoverer may set custom_name. Display resolves custom_name -> auto_name
    # -> legacy name. Discovery (separate from sector discovery) records the
    # first discoverer, who alone may rename (claimed or not).
    auto_name = Column(String(100), nullable=True)
    custom_name = Column(String(50), nullable=True)
    discovered_by = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    discovered_at = Column(DateTime(timezone=True), nullable=True)
    sector_id = Column(Integer, nullable=False)
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True)
    owner_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Planet properties
    type = Column(Enum(PlanetType, name="planet_type"), nullable=False)
    status = Column(Enum(PlanetStatus, name="planet_status"), nullable=False, default=PlanetStatus.UNINHABITABLE)
    size = Column(Integer, nullable=False, default=5)  # 1-10 scale
    position = Column(Integer, nullable=False, default=3)  # Position from star, affects conditions
    gravity = Column(Float, nullable=False, default=1.0)  # Earth g ratio
    planet_type = Column(String(50), nullable=True)  # Simple string type for API compatibility
    specialization = Column(String(50), nullable=True)
    
    # Habitability
    atmosphere = Column(String, nullable=True)  # Atmospheric composition
    temperature = Column(Float, nullable=False, default=0.0)  # Average temperature in Celsius
    water_coverage = Column(Float, nullable=False, default=0.0)  # Percentage of surface with water (0-100)
    habitability_score = Column(Integer, nullable=False, default=0)  # 0-100 scale
    radiation_level = Column(Float, nullable=False, default=0.0)  # 0.0-1.0 scale
    
    # Resources
    resource_richness = Column(Float, nullable=False, default=1.0)  # 0.0-3.0 multiplier
    resources = Column(JSONB, nullable=False, default={})  # Available resources
    special_resources = Column(ARRAY(String), nullable=False, default=[])  # Unique resources
    fuel_ore = Column(Integer, nullable=False, default=0)
    organics = Column(Integer, nullable=False, default=0)
    equipment = Column(Integer, nullable=False, default=0)
    fighters = Column(Integer, nullable=False, default=0)
    
    # Colonization
    colonized_at = Column(DateTime(timezone=True), nullable=True)
    population = Column(BigInteger, nullable=False, default=0)  # Current population
    max_population = Column(BigInteger, nullable=False, default=0)  # Maximum sustainable population
    population_growth = Column(Float, nullable=False, default=0.0)  # Growth rate percentage
    colonists = Column(Integer, nullable=False, default=0)
    # ADR-0035 "Schema impact": default 10000 -> 1000 (L1 Outpost scale)
    max_colonists = Column(Integer, nullable=False, default=1000)
    fuel_allocation = Column(Integer, nullable=False, default=0)
    organics_allocation = Column(Integer, nullable=False, default=0)
    equipment_allocation = Column(Integer, nullable=False, default=0)
    # Capital population hubs are public welcome worlds and never claimable
    # (SYSTEMS/galaxy-generation.md Step 8: "A population hub planet
    # (`is_population_hub = True`) ... Public, well-policed, non-destructible.")
    is_population_hub = Column(Boolean, nullable=False, default=False, server_default="false")
    # Anchor for lazy colonist growth (FEATURES/planets/colonization.md
    # "Population growth": colonist_rate = colonists × 0.01 × (habitability/100) per day).
    # Only whole-colonist time is consumed from the anchor; the fractional
    # remainder stays banked until it yields a full colonist.
    last_growth_at = Column(DateTime(timezone=True), nullable=True)
    
    # Economy and production
    economy = Column(JSONB, nullable=False, default={})  # Economic attributes
    production = Column(JSONB, nullable=False, default={  # Production settings
        "fuel": 0,
        "organics": 0,
        "equipment": 0,
        "research": 0
    })
    production_efficiency = Column(Float, nullable=False, default=1.0)  # 0.0-2.0 multiplier
    
    # Buildings
    factory_level = Column(Integer, nullable=False, default=0)
    farm_level = Column(Integer, nullable=False, default=0)
    mine_level = Column(Integer, nullable=False, default=0)
    research_level = Column(Integer, nullable=False, default=0)
    
    # Defense
    defense_level = Column(Integer, nullable=False, default=0)  # 0-10 scale
    shields = Column(Integer, nullable=False, default=0)
    weapon_batteries = Column(Integer, nullable=False, default=0)
    defense_turrets = Column(Integer, nullable=False, default=0)
    defense_shields = Column(Integer, nullable=False, default=0)
    defense_fighters = Column(Integer, nullable=False, default=0)
    
    # Status and events
    last_attacked = Column(DateTime(timezone=True), nullable=True)
    last_production = Column(DateTime(timezone=True), nullable=True)
    active_events = Column(JSONB, nullable=False, default=[])
    # CRT grid spine (WO-K1a): single-writer is structures.py. Nullable/additive — a planet with
    # null structures is a legacy planet that structures.seed() cold-starts on first settle().
    # Holds terraform_meta.last_settle_at (the spine monotonic gate / event-window key) and, in
    # K1b, the grid layout (plots/economy/lab/defense/build-queue). NEVER an ALTER of active_events.
    structures = Column(JSONB, nullable=True)
    # Landing-rights ACL (WO-G16; FEATURES/planets/colonization.md "Landing rights").
    # Additive/nullable — null ⇒ public (anyone may land; backward-compatible default).
    # Shape: {"mode": "public|team_only|private|whitelist|denylist",
    #         "whitelist": [player_uuid,...], "denylist": [player_uuid,...]}.
    # Enforced at land-time only (mode changes apply to subsequent landings; no
    # eviction of ships already on-planet). tax_rate is a SEPARATE, Max-gated axis.
    landing_rights = Column(JSONB, nullable=True)
    description = Column(String, nullable=True)

    # Siege information
    under_siege = Column(Boolean, nullable=False, default=False)
    siege_started_at = Column(DateTime(timezone=True), nullable=True)
    siege_attacker_id = Column(UUID(as_uuid=True), nullable=True)
    # Colony morale, 0-100 (DB column added in migration a1b2c3d4e5f6 with
    # server_default '100' — a fresh colony starts at full morale).
    morale = Column(Integer, nullable=False, default=100, server_default="100")
    # Consecutive turns enemies have been present (siege escalation counter,
    # DB column added in migration a1b2c3d4e5f6).
    siege_turns = Column(Integer, nullable=False, default=0, server_default="0")

    # Terraforming state (DB columns added in migration b2c3d4e5f6a7;
    # documented in FEATURES/planets/terraforming.md "Planet model state").
    # Per-level metadata (cost/boost/duration) lives in active_events JSONB
    # under a {type: "terraforming"} entry.
    terraforming_active = Column(Boolean, nullable=False, default=False, server_default="false")
    terraforming_target = Column(Integer, nullable=True)  # Target habitability score
    terraforming_start_time = Column(DateTime(timezone=True), nullable=True)
    terraforming_progress = Column(Float, nullable=False, default=0.0, server_default="0.0")
    
    # Citadel system
    citadel_level = Column(Integer, nullable=False, default=0)  # 0-5
    citadel_upgrading = Column(Boolean, nullable=False, default=False)
    citadel_upgrade_started_at = Column(DateTime(timezone=True), nullable=True)
    citadel_upgrade_complete_at = Column(DateTime(timezone=True), nullable=True)
    citadel_safe_credits = Column(BigInteger, nullable=False, default=0)
    citadel_safe_max = Column(BigInteger, nullable=False, default=0)
    citadel_drone_capacity = Column(Integer, nullable=False, default=0)
    citadel_max_population = Column(BigInteger, nullable=False, default=0)

    # Genesis device information
    genesis_created = Column(Boolean, nullable=False, default=False)
    genesis_device_id = Column(UUID(as_uuid=True), ForeignKey("genesis_devices.id"), nullable=True)
    genesis_tier = Column(String(20), nullable=True)  # basic / enhanced / advanced
    formation_status = Column(String(20), nullable=True)  # forming / complete
    formation_started_at = Column(DateTime(timezone=True), nullable=True)
    formation_complete_at = Column(DateTime(timezone=True), nullable=True)
    
    # Regional association
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=True)
    
    # Relationships
    owner = relationship("Player", secondary=player_planets, back_populates="planets")
    sector = relationship("Sector", foreign_keys=[sector_uuid], back_populates="planets")
    genesis_device = relationship("GenesisDevice", foreign_keys=[genesis_device_id], back_populates="planet")
    formation = relationship("PlanetFormation", foreign_keys="[PlanetFormation.resulting_planet_id]", back_populates="resulting_planet", uselist=False)
    region = relationship("Region", back_populates="planets")
    
    @property
    def display_name(self) -> str:
        """The name shown to players: a discoverer's custom name wins, else the
        generated auto-name, else the legacy stored name (ADR-0073)."""
        return self.custom_name or self.auto_name or self.name

    def __repr__(self):
        return f"<Planet {self.name} ({self.type.name}) - Sector: {self.sector_id}, Status: {self.status.name}>"