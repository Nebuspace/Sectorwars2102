import uuid
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, Table, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.sector import Sector
    from src.models.resource import Market


# Association table for player-station relationship
player_stations = Table(
    "player_stations",
    Base.metadata,
    Column("player_id", UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
    Column("station_id", UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True),
    Column("acquired_at", DateTime(timezone=True), server_default=func.now(), nullable=False)
)


class StationClass(enum.Enum):
    CLASS_0 = 0   # Sol System - Special mechanics
    CLASS_1 = 1   # Mining Operation
    CLASS_2 = 2   # Agricultural Center
    CLASS_3 = 3   # Industrial Hub
    CLASS_4 = 4   # Distribution Center
    CLASS_5 = 5   # Collection Hub
    CLASS_6 = 6   # Mixed Market
    CLASS_7 = 7   # Resource Exchange
    CLASS_8 = 8   # Black Hole (Premium Buyer)
    CLASS_9 = 9   # Nova (Premium Seller)
    CLASS_10 = 10 # Luxury Market
    CLASS_11 = 11 # Premium Tech Specialist (buys+sells exotic_technology & luxury_goods, +25% both directions)

class StationType(enum.Enum):
    TRADING = "TRADING"          # Commercial hub, good prices
    MILITARY = "MILITARY"        # Security forces, weapons
    INDUSTRIAL = "INDUSTRIAL"    # Manufacturing focus
    MINING = "MINING"            # Resource extraction focus
    SCIENTIFIC = "SCIENTIFIC"    # Research and technology
    SHIPYARD = "SHIPYARD"        # Ship construction and repair
    OUTPOST = "OUTPOST"          # Basic frontier installation
    BLACK_MARKET = "BLACK_MARKET"  # Illegal goods, high risk
    DIPLOMATIC = "DIPLOMATIC"    # Faction embassy and neutral ground
    CORPORATE = "CORPORATE"      # Corporation headquarters


class StationStatus(enum.Enum):
    OPERATIONAL = "OPERATIONAL"
    DAMAGED = "DAMAGED"
    UNDER_CONSTRUCTION = "UNDER_CONSTRUCTION"
    UNDER_ATTACK = "UNDER_ATTACK"
    LOCKDOWN = "LOCKDOWN"
    ABANDONED = "ABANDONED"
    RESTRICTED = "RESTRICTED"

class TraderPersonalityType(enum.Enum):
    FEDERATION = "FEDERATION"  # Formal, rule-following
    BORDER = "BORDER"          # Practical, honest
    FRONTIER = "FRONTIER"      # Rugged, independent
    LUXURY = "LUXURY"          # Sophisticated, status-conscious
    BLACK_MARKET = "BLACK_MARKET"  # Suspicious, opportunistic


# Station-protection security tiers, in ascending order of protection
# (FEATURES/economy/station-protection.md § Security tiers). The integer RANK
# drives comparisons (none < basic < standard < premium) so callers compare
# ranks, never raw strings. An unknown/unconfigured tier ranks as "none" (0).
SECURITY_TIER_RANK = {
    "none": 0,
    "basic": 1,
    "standard": 2,
    "premium": 3,
}
# Guarantee #1 threshold: protection engages at "basic" and above.
SECURITY_TIER_PROTECTED_MIN_RANK = SECURITY_TIER_RANK["basic"]


def security_tier_rank(tier: Optional[str]) -> int:
    """Map a security-tier string to its ordered rank (none<basic<standard<premium).

    An unknown or missing tier ranks as ``none`` (0) — the conservative default.
    """
    return SECURITY_TIER_RANK.get((tier or "none").lower(), 0)


class Station(Base):
    __tablename__ = "stations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    sector_id = Column(Integer, nullable=False)
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True)
    owner_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    # Per-station 1s price-recompute rate limit (ADR-0051 SK30, WO-DBB-EC4).
    # last_price_recomputed_at is the wall-clock anchor of the most recent
    # full price recompute via TradingService.maybe_recompute_price; a second
    # recompute within EC4_RECOMPUTE_WINDOW_SECONDS is suppressed and instead
    # flags pending_price_recomputation, which the npc_scheduler periodic
    # flush_pending_recomputes sweep clears by repricing the deferred station.
    # NOTE: this is a WALL-CLOCK debounce on the hot read path, ORTHOGONAL to
    # the existing REGEN_TICK_HOURS canonical-hours lazy regen gate.
    last_price_recomputed_at = Column(DateTime(timezone=True), nullable=True)
    pending_price_recomputation = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    
    # Station properties
    station_class = Column(Enum(StationClass, name="station_class"), nullable=False)
    type = Column(Enum(StationType, name="station_type"), nullable=False)
    status = Column(Enum(StationStatus, name="station_status"), nullable=False, default=StationStatus.OPERATIONAL)
    size = Column(Integer, nullable=False, default=5)  # 1-10 scale
    
    # Economy and Trading
    faction_affiliation = Column(String, nullable=True)  # Which faction controls this station
    trade_volume = Column(Integer, nullable=False, default=100)  # Daily trade credits
    market_volatility = Column(Integer, nullable=False, default=50)  # 0-100, price fluctuation factor
    
    # Commodities - comprehensive trading data matching DATA_DEFS
    commodities = Column(JSONB, nullable=False, default={
        "ore": {
            "quantity": 1000, "capacity": 5000, "base_price": 15, "current_price": 15,
            "production_rate": 100, "price_variance": 20, "buys": False, "sells": True
        },
        "organics": {
            "quantity": 800, "capacity": 3000, "base_price": 18, "current_price": 18,
            "production_rate": 80, "price_variance": 25, "buys": True, "sells": False
        },
        "equipment": {
            "quantity": 500, "capacity": 2000, "base_price": 35, "current_price": 35,
            "production_rate": 50, "price_variance": 30, "buys": True, "sells": True
        },
        "fuel": {
            "quantity": 1500, "capacity": 4000, "base_price": 12, "current_price": 12,
            "production_rate": 120, "price_variance": 15, "buys": False, "sells": True
        },
        "luxury_goods": {
            "quantity": 200, "capacity": 800, "base_price": 100, "current_price": 100,
            "production_rate": 20, "price_variance": 40, "buys": False, "sells": False
        },
        "gourmet_food": {
            "quantity": 150, "capacity": 600, "base_price": 80, "current_price": 80,
            "production_rate": 15, "price_variance": 35, "buys": False, "sells": False
        },
        "exotic_technology": {
            "quantity": 50, "capacity": 200, "base_price": 250, "current_price": 250,
            "production_rate": 5, "price_variance": 50, "buys": False, "sells": False
        },
        "colonists": {
            "quantity": 100, "capacity": 500, "base_price": 50, "current_price": 50,
            "production_rate": 10, "price_variance": 10, "buys": False, "sells": False
        },
        # 9th commodity per ADR-0062 E-D1 (band 80-180 cr/unit; 130 is midpoint).
        # Bang's content.ts emits 9-commodity wire including precious_metals.
        "precious_metals": {
            "quantity": 80, "capacity": 400, "base_price": 130, "current_price": 130,
            "production_rate": 8, "price_variance": 30, "buys": False, "sells": False
        }
    })
    
    # AI Trader Personality for haggling system.
    #
    # SHAPE RECONCILED to DATA_MODELS/jsonb-schema.md § Station.trader_personality
    # (ADR-0079 schema-reconcile, WO-BO step A): `memory_duration_days` (NOT the
    # legacy `memory_duration`); `trust_level` defaults to 0 on the [-1000, 1000]
    # scale (NOT the meaningless legacy 50); appeal types drawn from the canonical
    # vocabulary. This Border default matches the jsonb-schema Border row
    # (difficulty 5, [economic, personal], 30 days). The additive `player_memory`
    # sub-doc holds per-player haggle history + trust for the 90-day memory
    # contract (Max #7). Single source of truth: core/trader_personalities.py.
    #
    # NB: this default only applies to NEW rows whose creator omits the column.
    # Existing rows carry the OLD shape and are normalized on read by the haggle
    # engine + reconciled in bulk by the startup personality-seeding backfill.
    # JSONB column → no migration (the shape change is data, not DDL).
    trader_personality = Column(JSONB, nullable=False, default={
        "type": "BORDER",
        "haggling_difficulty": 5,
        "preferred_appeal_types": ["economic", "personal"],
        "memory_duration_days": 30,
        "trust_level": 0,
        "quirks": [],
        "player_memory": {},
    })
    
    # Owner-set price adjustments. Also carries the ADR-0062 E-D3 station
    # MARKETING lever under the "price_adjustment_lever" key (+/-10%), applied
    # last in the canonical price stack and SKIPPED for same-owner/team buyers
    # (E-F1). Stored in this existing JSONB — no migration (stranded alembic
    # head). See trading_service.compute_station_lever_multiplier.
    price_modifiers = Column(JSONB, nullable=False, default={})
    
    # Services - comprehensive service offerings
    services = Column(JSONB, nullable=False, default={
        "ship_dealer": False,
        "ship_repair": True,
        "ship_maintenance": True,
        "ship_upgrades": False,
        "insurance": False,
        "drone_shop": False,
        "genesis_dealer": False,
        "mine_dealer": False,
        "diplomatic_services": False,
        "storage_rental": False,
        "market_intelligence": False,
        "refining_facility": False,
        "luxury_amenities": False
    })
    service_prices = Column(JSONB, nullable=False, default={})  # Prices for services
    
    # Defense - comprehensive defensive capabilities
    #
    # WO-BP-a (station-defense kernel): the trailing keys (hull_armor,
    # shield_pool, defensive_fire, point_defense_rating) make a station a
    # FORMIDABLE deterrent — Max: "stations are really really powerful" =
    # DEFENSE + DETERRENCE, not capture. These are read by
    # combat_service._resolve_port_combat to shred an attacker's drone swarm
    # and repel the assault decisively. Magnitudes are NO-CANON, deliberately
    # STRONG (orchestrator-blessed pending). Additive JSONB default only — NO
    # migration; existing rows fall back to the .get() defaults in the
    # resolver, so legacy stations are equally formidable.
    defenses = Column(JSONB, nullable=False, default={
        "defense_drones": 0,
        "max_defense_drones": 50,
        "auto_turrets": False,
        "defense_grid": False,
        "shield_strength": 50,
        "patrol_ships": 0,
        "military_contract": False,
        # --- station-defense kernel (WO-BP-a) ---
        "hull_armor": 5000,          # station structural HP — must be ground down to 0 to "capture"; astronomically high vs any realistic ship
        "shield_pool": 4000,         # regenerating barrier absorbed before hull_armor takes damage
        "shield_regen": 200,         # shield_pool restored per round (a sustained siege barely dents it)
        "defensive_fire": 120,       # raw defensive-fire strength per round — heavily attrites the attacker drone swarm
        "point_defense_rating": 30   # extra drones swatted per round by dedicated point-defense (anti-swarm)
    })
    
    # Station protection / security tier (FEATURES/economy/station-protection.md
    # § Security tiers). Carries a security TIER under the "tier" key, one of
    # four ordered levels: none(0) < basic(1) < standard(2) < premium(3). This is
    # the FIRST slice of the station-protection system (WO-CB1, no-attack-on-
    # docked-ships); tractor/guards/NPC-archetype/anti-theft/anti-board are
    # separate WOs and DO NOT live here yet.
    #
    # NO-CANON micro-decision (orchestrator-blessed pending): an UNCONFIGURED
    # station — security NULL, or a dict with no "tier" key — reads as tier
    # "none". This is deliberately conservative so existing/populated rows get
    # NO new protection until a station is explicitly seeded (no surprise
    # behavior flip). Additive nullable JSONB → see the WO-CB1 migration.
    # Canon DEFAULTS (player-owned→basic, operator-managed→standard/premium,
    # frontier/lawless→none) are SEEDED by the larger system, NOT here.
    security = Column(JSONB, nullable=True)

    # Ownership and Management
    ownership = Column(JSONB, nullable=True, default=None)  # Player ownership details
    
    # Market and timing
    last_market_update = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    market_update_frequency = Column(Integer, nullable=False, default=6)  # Hours between updates
    reputation_threshold = Column(Integer, nullable=False, default=0)  # Min reputation for docking
    
    # Special properties
    is_quest_hub = Column(Boolean, nullable=False, default=False)
    is_faction_headquarters = Column(Boolean, nullable=False, default=False)
    is_player_ownable = Column(Boolean, nullable=False, default=True)
    # SpaceDock flag (bang's Port.isSpaceDock sentinel; canonical at gameserver-side
    # too because StarDock-special-location hosts also get this flag for queries
    # that don't load the parent sector's special_features array).
    is_spacedock = Column(Boolean, nullable=False, default=False, server_default="false")
    # Central Nexus Starport Prime discriminator (FEATURES/economy/docking-slips
    # §Per-station-class slip counts). Both Starport Prime and a regional Capital
    # are StationClass.CLASS_0, but their docking-slip pools differ: Starport
    # Prime carries 200 transient / 50 long-term, a regional Capital 80 / 30.
    # This flag lets docking_service tell them apart. Set ONLY on the single
    # Central Nexus Starport Prime station (see nexus_generation_service).
    is_starport_prime = Column(Boolean, nullable=False, default=False, server_default="false")
    # TradeDock tier (FEATURES/economy/tradedock-shipyard): 'A' = Warp-Jumper-
    # capable (specialized construction slips), 'B' = standard construction.
    # NULL = not a TradeDock. TradeDocks are NPC-neutral, never ownable.
    tradedock_tier = Column(String(1), nullable=True)
    # Station treasury — docking fees and trade tax accrue here
    # (FEATURES/economy/port-ownership: the station as a small business)
    treasury_balance = Column(Integer, nullable=False, default=0, server_default="0")
    # Trade tax actually charged on buy/sell; previously a phantom getattr
    # default. Owners adjust within bounds (port-ownership tariff lever).
    tax_rate = Column(Float, nullable=False, default=0.10, server_default="0.10")
    
    # Acquisition requirements for player ownership
    acquisition_requirements = Column(JSONB, nullable=False, default={
        "min_trade_volume": 100000,
        "min_faction_standing": "NEUTRAL",
        "base_price": 500000,
        "special_missions": []
    })
    
    # Status and events
    last_attacked = Column(DateTime(timezone=True), nullable=True)
    is_destroyed = Column(Boolean, nullable=False, default=False)
    recovery_time = Column(DateTime(timezone=True), nullable=True)
    active_events = Column(JSONB, nullable=False, default=[])
    description = Column(String, nullable=True)
    special_services = Column(ARRAY(String), nullable=False, default=[])
    
    # Regional association
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=True)
    
    # Relationships
    owner = relationship("Player", secondary=player_stations, back_populates="stations")
    sector = relationship("Sector", foreign_keys=[sector_uuid], back_populates="stations")
    market = relationship("Market", back_populates="station", uselist=False, cascade="all, delete-orphan")
    region = relationship("Region", back_populates="stations")
    
    def __repr__(self):
        return f"<Station {self.name} (Class {self.station_class.value}, {self.type.name}) - Sector: {self.sector_id}, Status: {self.status.name}>"
    
    @property
    def security_level(self) -> str:
        """Station-protection security tier as a lowercased string
        (FEATURES/economy/station-protection.md § Security tiers).

        Reads the "tier" key from the ``security`` JSONB. Defaults to "none"
        when ``security`` is NULL or has no/blank "tier" key — the conservative
        NO-CANON default (WO-CB1): an unconfigured station grants NO protection
        until explicitly seeded. Use :func:`security_tier_rank` to COMPARE tiers
        (none<basic<standard<premium), never a raw string ``==``.

        Defensive: a non-dict ``security`` value (a future seeder bug writing a
        scalar/list) reads as "none" rather than raising inside attack_player."""
        sec = self.security if isinstance(self.security, dict) else {}
        tier = sec.get("tier")
        return (tier or "none").lower()

    @property
    def security_rank(self) -> int:
        """Ordered rank of this station's security tier (none=0<basic=1<
        standard=2<premium=3). Convenience over ``security_tier_rank``."""
        return security_tier_rank(self.security_level)

    @property
    def price_adjustment_lever(self) -> float:
        """ADR-0062 E-D3 station marketing lever (+/-10%), stored in the
        price_modifiers JSONB. 0.0 (neutral) by default. Bounds + the E-F1
        same-owner/team skip are enforced in trading_service; this accessor
        returns the raw stored value."""
        return float((self.price_modifiers or {}).get("price_adjustment_lever", 0.0) or 0.0)

    def get_trading_pattern(self):
        """Get what this station buys/sells based on its class.

        Single source of truth lives in ``src.core.station_class_map``
        (imported lazily — that module imports StationClass from here).
        """
        from src.core.station_class_map import get_class_pattern
        return get_class_pattern(self.station_class)

    def update_commodity_trading_flags(self):
        """Update commodity buy/sell flags based on port class.

        Delegates to :func:`src.core.station_class_map.apply_trading_flags`
        (in-place, same behaviour as the original inline implementation).
        """
        from src.core.station_class_map import apply_trading_flags
        apply_trading_flags(self.commodities, self.station_class)

    def update_commodity_stock_levels(self):
        """Update commodity stock levels to match port's trading role.

        Delegates to :func:`src.core.station_class_map.apply_stock_levels`
        with an unseeded RNG, matching the original module-level
        ``random.uniform`` behaviour.
        """
        import random

        from src.core.station_class_map import apply_stock_levels
        apply_stock_levels(self.commodities, self.station_class, random.Random()) 