import uuid
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, Table, func
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
    CLASS_11 = 11 # Advanced Tech Hub

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


class Station(Base):
    __tablename__ = "stations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    sector_id = Column(Integer, nullable=False)
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True)
    owner_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
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
    
    # AI Trader Personality for haggling system
    trader_personality = Column(JSONB, nullable=False, default={
        "type": "BORDER",
        "haggling_difficulty": 5,
        "preferred_appeal_types": ["survival", "logical"],
        "memory_duration": 7,
        "trust_level": 50,
        "quirks": []
    })
    
    price_modifiers = Column(JSONB, nullable=False, default={})  # Owner-set price adjustments
    
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
    defenses = Column(JSONB, nullable=False, default={
        "defense_drones": 0,
        "max_defense_drones": 50,
        "auto_turrets": False,
        "defense_grid": False,
        "shield_strength": 50,
        "patrol_ships": 0,
        "military_contract": False
    })
    
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
    
    def get_trading_pattern(self):
        """Get what this station buys/sells based on its class."""
        patterns = {
            StationClass.CLASS_0: {"buys": ["special_goods"], "sells": ["special_goods", "colonists"]},
            StationClass.CLASS_1: {"buys": ["ore"], "sells": ["organics", "equipment"]},
            StationClass.CLASS_2: {"buys": ["organics"], "sells": ["ore", "equipment"]},
            StationClass.CLASS_3: {"buys": ["equipment"], "sells": ["ore", "organics"]},
            StationClass.CLASS_4: {"buys": ["exotic_technology"], "sells": ["ore", "organics", "equipment", "fuel"]},
            StationClass.CLASS_5: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": ["luxury_goods"]},
            StationClass.CLASS_6: {"buys": ["ore", "organics"], "sells": ["equipment", "fuel"]},
            StationClass.CLASS_7: {"buys": ["equipment", "fuel"], "sells": ["ore", "organics"]},
            StationClass.CLASS_8: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": []},
            StationClass.CLASS_9: {"buys": [], "sells": ["ore", "organics", "equipment", "fuel"]},
            StationClass.CLASS_10: {"buys": ["gourmet_food"], "sells": ["luxury_goods", "exotic_technology"]},
            StationClass.CLASS_11: {"buys": ["exotic_technology"], "sells": ["advanced_components"]}
        }
        return patterns.get(self.station_class, {"buys": [], "sells": []})
    
    def update_commodity_trading_flags(self):
        """Update commodity buy/sell flags based on port class."""
        pattern = self.get_trading_pattern()
        
        # Reset all flags
        for commodity in self.commodities:
            self.commodities[commodity]["buys"] = False
            self.commodities[commodity]["sells"] = False
        
        # Set flags based on trading pattern
        for commodity in pattern.get("buys", []):
            if commodity in self.commodities:
                self.commodities[commodity]["buys"] = True
                
        for commodity in pattern.get("sells", []):
            if commodity in self.commodities:
                self.commodities[commodity]["sells"] = True
    
    def update_commodity_stock_levels(self):
        """Update commodity stock levels to match port's trading role."""
        import random
        
        pattern = self.get_trading_pattern()
        is_premium_seller = self.station_class == StationClass.CLASS_9  # Nova
        is_premium_buyer = self.station_class == StationClass.CLASS_8   # Black Hole
        is_distribution = self.station_class == StationClass.CLASS_4    # Distribution Center
        is_collection = self.station_class == StationClass.CLASS_5     # Collection Hub
        
        for commodity_name, commodity_data in self.commodities.items():
            base_capacity = commodity_data.get("capacity", 1000)
            
            # Determine stock level based on port's role with this commodity
            if commodity_name in pattern.get("sells", []):
                # Station sells this commodity - needs high stock
                if is_premium_seller:
                    # Premium sellers have maximum stock
                    stock_level = int(base_capacity * random.uniform(0.8, 1.0))
                    production_rate = commodity_data.get("production_rate", 50) * 2
                elif is_distribution:
                    # Distribution centers have very high stock for selling
                    stock_level = int(base_capacity * random.uniform(0.7, 0.9))
                    production_rate = commodity_data.get("production_rate", 50) * 1.5
                else:
                    # Regular sellers have good stock
                    stock_level = int(base_capacity * random.uniform(0.4, 0.7))
                    production_rate = commodity_data.get("production_rate", 50)
                    
            elif commodity_name in pattern.get("buys", []):
                # Station buys this commodity - needs low stock, high capacity
                if is_premium_buyer or is_collection:
                    # Premium buyers and collection hubs have minimal stock, maximum capacity
                    stock_level = int(base_capacity * random.uniform(0.05, 0.15))
                    production_rate = 0  # They don't produce, they collect
                else:
                    # Regular buyers have low stock
                    stock_level = int(base_capacity * random.uniform(0.1, 0.3))
                    production_rate = 0
            else:
                # Station doesn't trade this commodity - minimal stock
                stock_level = int(base_capacity * random.uniform(0.1, 0.25))
                production_rate = commodity_data.get("production_rate", 10)
            
            # Ensure minimum stock of 1 for all commodities
            stock_level = max(1, stock_level)
            
            # Update the commodity data
            self.commodities[commodity_name]["quantity"] = stock_level
            self.commodities[commodity_name]["production_rate"] = production_rate
            
            # Adjust pricing for premium ports
            base_price = commodity_data.get("base_price", 50)
            if is_premium_seller and commodity_name in pattern.get("sells", []):
                # Premium sellers charge less (better deals for players)
                self.commodities[commodity_name]["current_price"] = int(base_price * 0.8)
            elif is_premium_buyer and commodity_name in pattern.get("buys", []):
                # Premium buyers pay more (better deals for players)
                self.commodities[commodity_name]["current_price"] = int(base_price * 1.3)
            else:
                self.commodities[commodity_name]["current_price"] = base_price 