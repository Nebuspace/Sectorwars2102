import uuid
import enum
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB

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

    Categories (registry category strings — see resource_registry_seeder):
    - Core Commodities (7): Basic station-traded resources
    - Strategic Resources (4): Advanced gameplay materials
    - Rare Materials (3): Endgame finds + precious_metals (Secondary mining drop)
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

    # Rare Materials (3) — PRECIOUS_METALS is CATEGORY_RARE in the seeder
    # (priced Secondary mining drop); prismatic_ore / lumen_crystals unpriced.
    PRECIOUS_METALS = "PRECIOUS_METALS"
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
    # NOTE (WO-ARCH-RES-1-KERNEL / WO-ARCH-RES-2 follow-up): the registry
    # seeder (resource_registry_seeder.py) upserts one row per ResourceType
    # via query-then-upsert, same idempotency pattern as
    # ship_specifications_seeder — application-level uniqueness was
    # sufficient for a single-threaded startup seed at kernel time.
    # unique=True added here (Orchestrator-approved additive follow-up) is
    # now backed by a real DB constraint — see alembic revision
    # 5a30b799bb25 (authored, NOT applied; the migration itself is additive
    # but requires no pre-existing duplicate `type` rows to apply — see that
    # revision's docstring).
    type = Column(Enum(ResourceType, name="resource_type"), nullable=False, unique=True)
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


# NOTE: the `Market` model (WO-RETIRE-RESOURCE-BLACK-MARKET-MODEL) was removed
# here — dead ORM class, zero readers/writers anywhere in src/ beyond its own
# models/__init__.py import and Station.market's relationship. The live
# market/pricing surface is MarketPrice (market_transaction.py). This is a
# code-level retirement only: the `markets` table itself was NOT dropped
# (schema drops are a separate, explicitly-gated migration decision).