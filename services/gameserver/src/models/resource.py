import uuid
import enum
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class ResourceType(enum.Enum):
    """
    Resource type enumeration for database models.

    IMPORTANT NAMING CONVENTION NOTE:
    ================================
    This enum uses UPPER_CASE names (BASIC_FOOD, TECHNOLOGY, POPULATION) which
    differ from the lowercase_underscore names used throughout the rest of the
    codebase (organics, equipment, colonists).

    The actual codebase standard is:
    - Database columns: planet.organics, planet.equipment, team.treasury_organics
    - Services: Use string literals like "organics", "equipment", "colonists"
    - Frontend: Uses lowercase_underscore names throughout

    Name Mapping:
    - BASIC_FOOD -> organics (in actual code)
    - TECHNOLOGY -> equipment (in actual code)
    - POPULATION -> colonists (in actual code)

    This enum is primarily used by the Resource model. Most trading flows
    use string-based commodity names directly.

    See /DOCS/STATUS/COMMODITY_NAMING_ANALYSIS.md for detailed analysis.

    Categories:
    - Core Commodities (7): Basic trading resources
    - Strategic Resources (4): Advanced gameplay materials
    - Rare Materials (2): Endgame high-value materials
    """

    # Core Commodities (7)
    # NOTE: BASIC_FOOD maps to "organics" in actual trading code
    # NOTE: TECHNOLOGY maps to "equipment" in actual trading code
    ORE = "ORE"
    BASIC_FOOD = "BASIC_FOOD"  # Actual codebase uses: "organics"
    GOURMET_FOOD = "GOURMET_FOOD"
    FUEL = "FUEL"
    TECHNOLOGY = "TECHNOLOGY"  # Actual codebase uses: "equipment"
    EXOTIC_TECHNOLOGY = "EXOTIC_TECHNOLOGY"
    LUXURY_GOODS = "LUXURY_GOODS"

    # Strategic Resources (4)
    # NOTE: POPULATION maps to "colonists" in actual trading code
    POPULATION = "POPULATION"  # Actual codebase uses: "colonists"
    QUANTUM_SHARDS = "QUANTUM_SHARDS"
    QUANTUM_CRYSTALS = "QUANTUM_CRYSTALS"
    COMBAT_DRONES = "COMBAT_DRONES"

    # Rare Materials (2)
    PRISMATIC_ORE = "PRISMATIC_ORE"
    PHOTONIC_CRYSTALS = "PHOTONIC_CRYSTALS"


class ResourceQuality(enum.Enum):
    LOW = "LOW"
    STANDARD = "STANDARD"
    HIGH = "HIGH"
    PREMIUM = "PREMIUM"
    EXOTIC = "EXOTIC"


class Resource(Base):
    __tablename__ = "resources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Identification
    # NOTE (WO-ARCH-RES-1-KERNEL): the registry seeder (resource_registry_
    # seeder.py) upserts one row per ResourceType via query-then-upsert,
    # same idempotency pattern as ship_specifications_seeder — no DB-level
    # uniqueness needed for a single-threaded startup seed. Deliberately NOT
    # adding unique=True here (would need a migration-level constraint add,
    # outside the additive-only nullable-columns/new-tables allowance).
    type = Column(Enum(ResourceType, name="resource_type"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String, nullable=True)
    
    # Properties
    base_value = Column(Integer, nullable=False)  # Base credit value per unit
    quality = Column(Enum(ResourceQuality, name="resource_quality"), nullable=False, default=ResourceQuality.STANDARD)
    value_multiplier = Column(Float, nullable=False, default=1.0)  # Modifier based on quality
    weight = Column(Float, nullable=False, default=1.0)  # Cargo space units
    
    # Market properties
    trade_volume = Column(Integer, nullable=False, default=100)  # Units traded daily
    price_volatility = Column(Float, nullable=False, default=0.1)  # Price fluctuation range
    
    # Production properties
    base_production_rate = Column(Float, nullable=False, default=1.0)  # Units per production cycle
    production_difficulty = Column(Integer, nullable=False, default=1)  # 1-10 scale
    
    # Special attributes
    special_attributes = Column(JSONB, nullable=False, default={})  # Special properties
    required_technology = Column(String, nullable=True)  # Technology required for production
    
    # Game balance
    is_active = Column(Boolean, nullable=False, default=True)

    # ------------------------------------------------------------------
    # Registry catalog fields (WO-ARCH-RES-1-KERNEL). Additive to the
    # market-simulation columns above (base_value/quality/trade_volume/etc.,
    # unused pending a full simulation build-out) — these back the seeded
    # canon registry exposed by GET /api/resources. `name` above already
    # carries the canonical lowercase_underscore slug (e.g. "ore",
    # "gourmet_food") per the commodity-name convention documented on
    # ResourceType; `label` is the human-readable display form.
    # ------------------------------------------------------------------
    label = Column(String(100), nullable=True)  # display name, e.g. "Gourmet Food"
    icon = Column(String(50), nullable=True)  # frontend icon key (slug; no glyph mapping decided yet)
    category = Column(String(50), nullable=True, index=True)  # core_commodity | strategic_resource | rare_material
    base_price = Column(Integer, nullable=True)  # credits/unit catalog base price; null where canon gives none
    price_range_min = Column(Integer, nullable=True)  # dynamic-pricing clamp floor; null where canon gives none
    price_range_max = Column(Integer, nullable=True)  # dynamic-pricing clamp ceiling; null where canon gives none
    is_storable = Column(Boolean, nullable=False, default=False)  # citadel-safe eligible (commodity_economy.SAFE_STORABLE_COMMODITIES)
    is_producible = Column(Boolean, nullable=False, default=False)  # station production_rate regen mechanic applies

    def __repr__(self):
        return f"<Resource {self.name} ({self.type.name}) - {self.quality.name} quality>"


# Market model to track resource transactions and prices
class Market(Base):
    __tablename__ = "markets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Market location
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    
    # Market attributes
    specialization = Column(String, nullable=True)  # What this market specializes in
    size = Column(Integer, nullable=False, default=5)  # 1-10 scale
    tax_rate = Column(Float, nullable=False, default=0.05)  # 5% default
    economic_status = Column(String, nullable=False, default="stable")  # boom, bust, stable, etc.
    
    # Inventory and pricing
    resource_availability = Column(JSONB, nullable=False, default={})  # Resource types to quantity
    resource_prices = Column(JSONB, nullable=False, default={})  # Resource types to price
    price_modifiers = Column(JSONB, nullable=False, default={})  # Factors affecting prices
    
    # Transaction history
    daily_volume = Column(JSONB, nullable=False, default={})  # Daily transaction volume
    price_history = Column(JSONB, nullable=False, default=[])  # Historical price records
    
    # Special features
    black_market = Column(Boolean, nullable=False, default=False)
    special_offers = Column(JSONB, nullable=False, default=[])
    trade_restrictions = Column(JSONB, nullable=False, default=[])
    
    # Relationships
    station = relationship("Station", back_populates="market")

    def __repr__(self):
        return f"<Market at {self.station.name} - Size: {self.size}>"