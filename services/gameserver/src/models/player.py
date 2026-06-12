import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.user import User
    from src.models.ship import Ship
    from src.models.team import Team
    from src.models.reputation import Reputation
    from src.models.sector import Sector
    from src.models.combat_log import CombatLog
    from src.models.warp_tunnel import WarpTunnel
    from src.models.genesis_device import GenesisDevice
    from src.models.resource import MarketTransaction
    from src.models.first_login import FirstLoginSession, PlayerFirstLoginState
    from src.models.region import Region, RegionalMembership, InterRegionalTravel
    from src.models.enhanced_ai_models import AIComprehensiveAssistant


class Player(Base):
    __tablename__ = "players"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    nickname = Column(String(50), nullable=True, default=None)  # Optional in-game name different from username
    credits = Column(Integer, nullable=False, default=10000)
    turns = Column(Integer, nullable=False, default=1000)
    # Monotonic count of turns ever spent (refunds decrement). NOT the
    # regenerating balance above — this is the cumulative clock ADR-0042
    # police arrival watchers compare against. Mutate ONLY through
    # turn_service.spend_turns/refund_turns.
    lifetime_turns_spent = Column(Integer, nullable=False, default=0)
    reputation = Column(JSONB, nullable=False, default={})  # Faction reputations

    # Personal Reputation System (good vs evil alignment)
    personal_reputation = Column(Integer, nullable=False, default=0)  # -1000 to +1000
    reputation_tier = Column(String(50), nullable=False, default="Neutral")  # Cached tier name
    name_color = Column(String(20), nullable=False, default="#FFFFFF")  # Cached color code

    # Military Ranking System (achievement-based progression)
    military_rank = Column(String(50), nullable=False, default="Recruit")  # Current rank
    rank_points = Column(Integer, nullable=False, default=0)  # Points toward next rank

    # ARIA consciousness tracking
    aria_bonus_multiplier = Column(Float, nullable=False, default=1.0)  # 1.0x to 1.5x
    aria_consciousness_level = Column(Integer, nullable=False, default=1)  # 1-5
    aria_relationship_score = Column(Integer, nullable=False, default=25)  # 0-100
    aria_total_interactions = Column(Integer, nullable=False, default=0)

    current_ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    home_sector_id = Column(Integer, nullable=False, default=1)
    current_sector_id = Column(Integer, nullable=False, default=1)
    is_docked = Column(Boolean, nullable=False, default=False)
    current_port_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)  # Station player is docked at
    is_landed = Column(Boolean, nullable=False, default=False)
    current_planet_id = Column(UUID(as_uuid=True), ForeignKey("planets.id", ondelete="SET NULL"), nullable=True)  # Planet player is landed on
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    attack_drones = Column(Integer, nullable=False, default=0)
    defense_drones = Column(Integer, nullable=False, default=0)
    mines = Column(Integer, nullable=False, default=0)
    # Quantum resource wallet (ADR-0009 venue split, ADR-0030 Quantum Jump).
    # Shards are the raw harvested resource; crystals are the assembled form
    # used for warp gate construction. Refined Quantum Charges live on the
    # Warp Jumper itself (ships.quantum_charges), not here.
    quantum_shards = Column(Integer, nullable=False, default=0, server_default=text("0"))
    quantum_crystals = Column(Integer, nullable=False, default=0, server_default=text("0"))
    genesis_devices = Column(Integer, nullable=False, default=0)
    insurance = Column(JSONB, nullable=True)
    last_game_login = Column(DateTime(timezone=True), nullable=True)  # Renamed from last_login to avoid confusion
    turn_reset_at = Column(DateTime(timezone=True), nullable=True)
    settings = Column(JSONB, nullable=False, default={})
    first_login = Column(JSONB, nullable=False, default={"completed": False})
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # When the player was created
    is_active = Column(Boolean, default=True, nullable=False)  # Player can be deactivated in-game
    
    # Multi-regional fields
    home_region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=True)
    current_region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id"), nullable=True)
    is_galactic_citizen = Column(Boolean, nullable=False, default=False)

    # Relationships
    user = relationship("User", back_populates="player")
    drones = relationship("Drone", back_populates="player", cascade="all, delete-orphan")
    drone_deployments = relationship("DroneDeployment", back_populates="player")
    current_ship = relationship("Ship", foreign_keys=[current_ship_id], post_update=True)
    ships = relationship("Ship", back_populates="owner", foreign_keys="Ship.owner_id")
    team = relationship("Team", back_populates="members")
    team_membership = relationship("TeamMember", back_populates="player", uselist=False, cascade="all, delete-orphan")
    faction_reputations = relationship("Reputation", back_populates="player", cascade="all, delete-orphan")
    
    # Many-to-many relationships
    planets = relationship("Planet", secondary="player_planets", back_populates="owner")
    stations = relationship("Station", secondary="player_stations", back_populates="owner")
    
    # New relationships
    discovered_sectors = relationship("Sector", back_populates="discovered_by")
    genesis_devices = relationship("GenesisDevice", back_populates="owner")
    combat_logs_as_attacker = relationship("CombatLog", foreign_keys="CombatLog.attacker_id", back_populates="attacker")
    combat_logs_as_defender = relationship("CombatLog", foreign_keys="CombatLog.defender_id", back_populates="defender")
    created_warp_tunnels = relationship("WarpTunnel", back_populates="created_by")
    market_transactions = relationship("src.models.resource.MarketTransaction", back_populates="player")
    enhanced_market_transactions = relationship("src.models.market_transaction.MarketTransaction", back_populates="player")
    first_login_sessions = relationship("FirstLoginSession", back_populates="player", cascade="all, delete-orphan")
    first_login_state = relationship("PlayerFirstLoginState", back_populates="player", uselist=False, cascade="all, delete-orphan")
    
    # Analytics relationships (TODO: Create PlayerSession and PlayerActivity models)
    # sessions = relationship("PlayerSession", back_populates="player", cascade="all, delete-orphan")
    # activities = relationship("PlayerActivity", cascade="all, delete-orphan")
    
    # Multi-regional relationships
    home_region = relationship("Region", foreign_keys=[home_region_id])
    current_region = relationship("Region", foreign_keys=[current_region_id])
    regional_memberships = relationship("RegionalMembership", back_populates="player", cascade="all, delete-orphan")
    inter_regional_travels = relationship("InterRegionalTravel", back_populates="player", cascade="all, delete-orphan")
    
    # AI Trading System relationships
    trading_profile = relationship("PlayerTradingProfile", back_populates="player", uselist=False, cascade="all, delete-orphan")
    ai_recommendations = relationship("AIRecommendation", back_populates="player", cascade="all, delete-orphan")
    
    # ARIA Personal Intelligence relationships
    aria_memories = relationship("ARIAPersonalMemory", back_populates="player", cascade="all, delete-orphan")
    aria_market_intelligence = relationship("ARIAMarketIntelligence", back_populates="player", cascade="all, delete-orphan")
    aria_exploration_map = relationship("ARIAExplorationMap", back_populates="player", cascade="all, delete-orphan")
    aria_trading_patterns = relationship("ARIATradingPattern", back_populates="player", cascade="all, delete-orphan")
    
    # Fleet relationships
    commanded_fleets = relationship("Fleet", back_populates="commander", foreign_keys="Fleet.commander_id")
    fleet_memberships = relationship("FleetMember", back_populates="player", cascade="all, delete-orphan")
    
    # Enhanced AI Assistant relationship
    ai_assistant = relationship("AIComprehensiveAssistant", back_populates="player", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Player {self.id} (User: {self.user_id})>"
    
    @property
    def is_team_leader(self) -> bool:
        if not self.team:
            return False
        return self.team.leader_id == self.id
        
    @property
    def username(self) -> str:
        """Return the player's display name - either the nickname or the username from the user account"""
        if self.nickname:
            return self.nickname
        if self.user:
            return self.user.username
        return "Unknown Player"
    
    # Multi-regional methods
    @property
    def can_travel_between_regions(self) -> bool:
        """Check if player can travel between regions"""
        return self.is_galactic_citizen or len(self.regional_memberships) > 1
    
    def get_regional_membership(self, region_id: str) -> Optional['RegionalMembership']:
        """Get player's membership in specific region"""
        for membership in self.regional_memberships:
            if str(membership.region_id) == region_id:
                return membership
        return None
    
    def get_reputation_in_region(self, region_id: str) -> int:
        """Get player's reputation score in specific region"""
        membership = self.get_regional_membership(region_id)
        return membership.reputation_score if membership else 0
    
    def can_vote_in_region(self, region_id: str) -> bool:
        """Check if player can vote in region's elections/referendums"""
        membership = self.get_regional_membership(region_id)
        return membership.can_vote if membership else False
    
    def join_region(self, region_id: str, membership_type: str = "visitor") -> 'RegionalMembership':
        """Create membership in a region"""
        from .region import RegionalMembership
        membership = RegionalMembership(
            player_id=self.id,
            region_id=region_id,
            membership_type=membership_type
        )
        self.regional_memberships.append(membership)
        return membership
    
    def leave_region(self, region_id: str) -> bool:
        """Remove membership from a region"""
        membership = self.get_regional_membership(region_id)
        if membership:
            self.regional_memberships.remove(membership)
            return True
        return False
    
    def travel_to_region(self, destination_region_id: str, travel_method: str = "platform_gate") -> 'InterRegionalTravel':
        """Initiate travel to another region"""
        from .region import InterRegionalTravel
        travel = InterRegionalTravel(
            player_id=self.id,
            source_region_id=self.current_region_id,
            destination_region_id=destination_region_id,
            travel_method=travel_method
        )
        self.inter_regional_travels.append(travel)
        return travel 