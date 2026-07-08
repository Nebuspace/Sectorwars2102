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
    # Monotonic count of colonists this player has ever LANDED onto a planet
    # (claim founding + disembark transfers). Counts the ACTUAL settled amount
    # after free-cap clamping — what truly decanted into a workforce. Drives the
    # `colonists_transported_lifetime` medal trigger (pioneer_office_pillar
    # @10,000). Embarking colonists back onto a ship does NOT decrement it.
    colonists_transported_lifetime = Column(Integer, nullable=False, default=0, server_default=text("0"))
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
    # Lumen Crystal ledger (WO-GWQ-LUMEN-FAUCET, ADR-0037). Two faucets credit
    # it: quantum_service.harvest_nebula's Emerald/Crimson drop roll, and the
    # Class-5+ refining_service.collect_lumen_refine conversion below.
    # NOTE: quantum-resources.md:235 names this field `lumen_crystal_inventory`
    # in prose; the master-queue WO chain (this WO + WO-GWQ-GATE-STAGING, which
    # reads Phase-3 lumen from "Player.lumen_crystals") specs the column as
    # `lumen_crystals` to mirror the quantum_crystals naming above. Built to
    # the WO spec for cross-WO consistency; flagged as a canon/WO naming
    # divergence for the doc to reconcile.
    lumen_crystals = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # Single in-flight Class-5+ Shard-to-Crystal (Lumen) refine job slot.
    # NULL == no job pending. refining_service.start_lumen_refine sets this to
    # scaled_deadline(12h) after debiting 100 Shards + 10,000 cr;
    # collect_lumen_refine credits +1 lumen_crystals once now() >= this and
    # clears it back to NULL. Only one job may be in flight at a time — start
    # is rejected while this is set (collect first). Non-exclusive of the
    # ship/station slot during the wait (NO-CANON, flagged to DECISIONS).
    lumen_refine_ready_at = Column(DateTime(timezone=True), nullable=True)
    genesis_devices = Column(Integer, nullable=False, default=0)
    insurance = Column(JSONB, nullable=True)
    last_game_login = Column(DateTime(timezone=True), nullable=True)  # Renamed from last_login to avoid confusion
    turn_reset_at = Column(DateTime(timezone=True), nullable=True)
    return_boost_until = Column(DateTime(timezone=True), nullable=True)  # WO-RE1: welcome-back ×1.5 emergent-rep window
    # ADR-0004: continuous turn regeneration anchor + stored cap.
    last_turn_regeneration = Column(DateTime(timezone=True), nullable=True)
    max_turns = Column(Integer, nullable=False, default=1000, server_default=text("1000"))
    # Suspect / Wanted lifecycle (Fringe/Federation contraband + bounty law).
    is_suspect = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_wanted = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    suspect_declared_at = Column(DateTime(timezone=True), nullable=True)
    wanted_declared_at = Column(DateTime(timezone=True), nullable=True)
    # Grey-flag PvP status (WO-BL, Max-ruled). A temporary "open season" mark
    # earned by aggressing on a lawful target:
    #   - attacking a GOOD-STANDING player → grey 1h (grey_kind="player_attack");
    #     while grey, GOOD-STANDING players may attack this player penalty-free.
    #   - attacking a STATION → grey 1 day (grey_kind="station_attack"); while
    #     grey, ANY player may attack this player penalty-free.
    # grey_until is the UTC expiry (NULL = not grey); a lesser later offense never
    # shortens it (MAX of existing/new). Cleared early by paying a fine. NO-CANON
    # numbers (durations / fines / good-standing threshold) — flagged for Max.
    grey_until = Column(DateTime(timezone=True), nullable=True)
    grey_kind = Column(String(20), nullable=True)  # "player_attack" | "station_attack"
    # Journey victory (rank-1 completion of the campaign).
    is_game_complete = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    rank_victory_at = Column(DateTime(timezone=True), nullable=True)
    settings = Column(JSONB, nullable=False, default={})
    first_login = Column(JSONB, nullable=False, default={"completed": False})
    # CRT WO-K0-2: the research ledger. ONE additive NULLABLE JSONB column — the
    # whole research kernel rides this single field (no per-system columns; the
    # tree is read at point-of-use, never written onto buffed entities). NULL is
    # the cold-start state and means rp:0 / unlocked:[t.root.0] — the lazy-seed
    # contract lives in research_service (NOT a DB default), so the seed is
    # deterministic and the WIPE+REFUND sweep can detect a never-swept player.
    # Shape: {"rp": int, "insight": int, "doctrine": int, "unlocked": [node_id],
    #         "swept_at": iso8601 | absent}. swept_at present == A.4 one-time
    #         wipe+refund already applied (idempotency anchor).
    research_ledger = Column(JSONB, nullable=True)
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
    # ADR-0045: per-player warp knowledge (latent-warp discovery state).
    warp_knowledge = relationship(
        "PlayerWarpKnowledge", back_populates="player", cascade="all, delete-orphan"
    )
    # WO-TF added a 2nd FK to players (port_owner_id) on MarketTransaction, so this
    # reverse relationship must declare foreign_keys to disambiguate the join (else
    # AmbiguousForeignKeysError). It pairs with MarketTransaction.player (player_id).
    enhanced_market_transactions = relationship(
        "src.models.market_transaction.MarketTransaction",
        back_populates="player",
        foreign_keys="src.models.market_transaction.MarketTransaction.player_id",
    )
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

    @classmethod
    def display_name_expr(cls, user_username_col=None, *, label: str = "username",
                           fallback: Optional[str] = "Unknown Player"):
        """SQL-expression twin of the `username` property above.

        `username` is a plain Python @property — it can't appear in a
        select()/query() column list, because "the linked User's username"
        isn't reachable without a join the caller controls. This reproduces
        the same fallback chain (nickname if truthy — '' counts as unset,
        matching the property's truthiness check — else the linked User's
        username) as a labeled SQLAlchemy expression, so every admin/
        governance read-path resolves display names identically instead of
        re-deriving the rule.

        Join recipe: the caller must already join `User` on
        `Player.user_id == User.id` (or an aliased equivalent) for the
        fallback to see it — pass that join's username column via
        `user_username_col` (defaults to `User.username` if omitted).

        `fallback` is the terminal literal for when BOTH nickname and the
        joined username are NULL (e.g. an outer join with no matching
        Player/User row at all) — defaults to "Unknown Player" to match the
        property exactly. Pass `fallback=None` to omit that terminal literal
        and let the expression resolve to SQL NULL instead, for call sites
        that were already relying on NULL-on-no-match (e.g. LEFT OUTER JOINs
        that intentionally return an unresolved sender as `null`, not a
        literal string) and must not add fabricated coalesce values.
        """
        if user_username_col is None:
            from src.models.user import User
            user_username_col = User.username
        args = [func.nullif(cls.nickname, ''), user_username_col]
        if fallback is not None:
            args.append(fallback)
        return func.coalesce(*args).label(label)

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