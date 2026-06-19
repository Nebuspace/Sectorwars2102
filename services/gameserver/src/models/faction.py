"""
Faction model for the Sectorwars2102 game.

Factions represent major political/economic entities that control territory,
influence market prices, and provide missions to players.
"""

from uuid import uuid4
from typing import List, Optional
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ARRAY, Enum as SQLEnum, ForeignKey, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from src.core.database import Base


class FactionType(str, enum.Enum):
    """Types of factions in the game."""
    FEDERATION = "Federation"
    INDEPENDENTS = "Independents"
    PIRATES = "Pirates"
    MERCHANTS = "Merchants"
    EXPLORERS = "Explorers"
    MILITARY = "Military"  # Code-wins: kept (predates ADR-0033's enum table).
    # ADR-0033: Astral Mining Consortium promoted to first-class faction type.
    MINING = "Mining"
    # ADR-0033: Fringe Alliance (clarified from generic outlaw) + Shadow Syndicate.
    OUTLAWS = "Outlaws"
    SYNDICATE = "Syndicate"
    # Galactic Concord — police / law-enforcement faction.
    CONCORD = "Concord"
    
    @classmethod
    def _missing_(cls, value):
        """Handle case-insensitive lookup."""
        for member in cls:
            if member.value.upper() == value.upper():
                return member
        return None


class FactionTypeDB(TypeDecorator):
    """Custom type to handle FactionType enum properly."""
    impl = String
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        """Convert enum to its value when storing."""
        if value is None:
            return value
        if isinstance(value, FactionType):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Convert stored value back to enum."""
        if value is None:
            return value
        return FactionType(value)


class Faction(Base):
    """
    Faction model representing major political/economic entities.
    
    Each faction controls territory, influences market prices, and provides
    missions to players. Player reputation with factions affects trading
    prices and access to faction-controlled sectors.
    """
    __tablename__ = "factions"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Basic information
    name = Column(String(100), unique=True, nullable=False, index=True)
    faction_type = Column(
        SQLEnum(FactionType, name='factiontype', create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False
    )
    description = Column(Text)
    
    # Territory control
    territory_sectors = Column(ARRAY(UUID(as_uuid=True)), default=list)
    home_sector_id = Column(UUID(as_uuid=True))  # Primary headquarters
    
    # Economic influence
    base_pricing_modifier = Column(Float, default=1.0)  # 0.8 = 20% discount, 1.2 = 20% markup
    trade_specialties = Column(ARRAY(String), default=list)  # Commodities they specialize in
    
    # Political stance
    aggression_level = Column(Integer, default=5)  # 1-10 scale, affects NPC behavior
    diplomacy_stance = Column(String(50), default="neutral")  # hostile, neutral, friendly
    
    # Visual/UI elements
    color_primary = Column(String(7))  # Hex color for UI
    color_secondary = Column(String(7))  # Hex color for UI
    logo_url = Column(String(255))
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    reputation_records = relationship("Reputation", back_populates="faction", cascade="all, delete-orphan")
    missions = relationship("FactionMission", back_populates="faction", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Faction(id={self.id}, name='{self.name}', type='{self.faction_type}')>"
    
    def get_pricing_modifier(self, player_reputation: int) -> float:
        """
        Calculate pricing modifier based on player reputation.
        
        Args:
            player_reputation: Player's reputation with this faction (-800 to +800)
            
        Returns:
            Float multiplier for prices (e.g., 0.8 = 20% discount)
        """
        # Base modifier
        modifier = self.base_pricing_modifier
        
        # Reputation adjustments
        if player_reputation >= 600:  # Honored
            modifier *= 0.85  # 15% discount
        elif player_reputation >= 400:  # Friendly
            modifier *= 0.92  # 8% discount
        elif player_reputation >= 200:  # Neutral+
            modifier *= 0.96  # 4% discount
        elif player_reputation <= -600:  # Hated
            modifier *= 1.30  # 30% markup
        elif player_reputation <= -400:  # Hostile
            modifier *= 1.20  # 20% markup
        elif player_reputation <= -200:  # Unfriendly
            modifier *= 1.10  # 10% markup
            
        return round(modifier, 2)
    
    def can_access_territory(self, player_reputation: int) -> bool:
        """
        Check if a player can access faction-controlled territory.
        
        Args:
            player_reputation: Player's reputation with this faction
            
        Returns:
            Boolean indicating if access is allowed
        """
        # Pirates and Military have stricter access controls
        if self.faction_type in [FactionType.PIRATES, FactionType.MILITARY]:
            return player_reputation >= -200  # Must not be hostile
        
        # Other factions are more lenient
        return player_reputation >= -400  # Can't be hated


class FactionMission(Base):
    """
    Missions offered by factions to players.
    
    Completing missions affects player reputation with the faction
    and may have consequences with other factions.
    """
    __tablename__ = "faction_missions"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Mission details
    faction_id = Column(UUID(as_uuid=True), ForeignKey("factions.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    mission_type = Column(String(50), nullable=False)  # cargo_delivery, combat, exploration, etc.
    
    # Requirements
    min_reputation = Column(Integer, default=-800)  # Minimum reputation to accept
    min_level = Column(Integer, default=1)
    
    # Rewards
    credit_reward = Column(Integer, default=0)
    reputation_reward = Column(Integer, default=0)  # Positive or negative
    item_rewards = Column(ARRAY(String), default=list)
    
    # Mission parameters
    target_sector_id = Column(UUID(as_uuid=True))
    cargo_type = Column(String(50))  # For delivery missions
    cargo_quantity = Column(Integer)  # For delivery missions
    target_faction_id = Column(UUID(as_uuid=True))  # For diplomatic/combat missions
    
    # Status
    is_active = Column(Integer, default=1)  # Boolean as integer for MySQL compatibility
    expires_at = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    faction = relationship("Faction", back_populates="missions")
    
    def __repr__(self):
        return f"<FactionMission(id={self.id}, title='{self.title}', faction_id={self.faction_id})>"