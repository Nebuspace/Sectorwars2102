"""
ARIA Personal Intelligence Models
Each player's ARIA builds unique market intelligence from their exploration

This creates a personal knowledge graph where:
- Players only see predictions for places they've visited
- Market patterns are learned from personal experience
- Intelligence quality improves with more exploration
- Data is completely isolated between players (GDPR compliant)
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Text, ForeignKey, Index, UniqueConstraint, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, UTC
import enum
import uuid

from src.core.database import Base


class ARIAPersonalMemory(Base):
    """
    Core memory system for player's ARIA instance
    Stores all learned patterns and experiences
    """
    __tablename__ = "aria_personal_memories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    
    # Memory metadata
    memory_type = Column(String(50), nullable=False)  # market, combat, exploration, social
    importance_score = Column(Float, default=0.5)  # 0-1, how significant this memory is
    confidence_level = Column(Float, default=0.5)  # 0-1, how certain ARIA is about this
    
    # Memory content (encrypted for privacy)
    memory_content = Column(JSON, nullable=False)  # Encrypted JSON data
    memory_hash = Column(String(64), nullable=False)  # For deduplication
    
    # Temporal data
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_accessed = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    access_count = Column(Integer, default=0)
    
    # Memory decay (some memories fade over time)
    decay_rate = Column(Float, default=0.01)  # How fast this memory fades
    current_strength = Column(Float, default=1.0)  # Current memory strength
    
    # Relationships
    player = relationship("Player", back_populates="aria_memories")
    
    __table_args__ = (
        Index("idx_aria_memory_player_type", "player_id", "memory_type"),
        Index("idx_aria_memory_importance", "importance_score"),
        UniqueConstraint("player_id", "memory_hash", name="uq_player_memory_hash"),
    )


class ARIAMarketIntelligence(Base):
    """
    Player's personal market intelligence gathered by ARIA
    Only contains data from ports/sectors they've visited
    """
    __tablename__ = "aria_market_intelligence"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    
    # Location data
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id"), nullable=True)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id"), nullable=False)
    
    # Commodity intelligence
    commodity = Column(String(50), nullable=False)
    
    # Price history (player's personal observations)
    price_observations = Column(JSON, default=list)  # [{price, timestamp, quantity}]
    average_price = Column(Float, nullable=True)
    price_volatility = Column(Float, default=0.0)
    
    # Pattern recognition
    identified_patterns = Column(JSON, default=list)  # ["morning_spike", "weekend_dip"]
    pattern_confidence = Column(JSON, default=dict)  # {pattern: confidence}
    
    # Predictive data (based on personal observations only)
    price_trend = Column(String(20))  # rising, falling, stable, cyclic
    next_prediction = Column(Float, nullable=True)  # Next predicted price
    prediction_confidence = Column(Float, default=0.0)  # 0-1
    prediction_timestamp = Column(DateTime(timezone=True), nullable=True)
    
    # Trading success tracking
    trades_executed = Column(Integer, default=0)
    successful_trades = Column(Integer, default=0)
    total_profit = Column(Float, default=0.0)
    
    # Intelligence quality
    data_points = Column(Integer, default=0)  # How many observations
    last_visit = Column(DateTime(timezone=True), nullable=True)
    intelligence_quality = Column(Float, default=0.0)  # 0-1, based on data recency and quantity
    
    # Relationships
    player = relationship("Player", back_populates="aria_market_intelligence")
    station = relationship("Station")
    sector = relationship("Sector")
    
    __table_args__ = (
        Index("idx_aria_intel_player_location", "player_id", "sector_id", "commodity"),
        Index("idx_aria_intel_quality", "intelligence_quality"),
        UniqueConstraint("player_id", "station_id", "commodity", name="uq_player_port_commodity"),
    )


class ARIAExplorationMap(Base):
    """
    Player's personal exploration history
    ARIA can only make predictions for visited locations
    """
    __tablename__ = "aria_exploration_maps"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id"), nullable=False)
    
    # Visit tracking
    first_visit = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_visit = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    visit_count = Column(Integer, default=1)
    
    # Intelligence gathering
    ports_discovered = Column(JSON, default=list)  # List of port IDs
    warp_tunnels_mapped = Column(JSON, default=list)  # List of tunnel IDs
    hazards_identified = Column(JSON, default=list)  # Environmental hazards
    
    # Sector intelligence
    market_volatility = Column(Float, default=0.0)  # Observed volatility
    safety_rating = Column(Float, default=0.5)  # 0-1, based on combat encounters
    trade_opportunity_score = Column(Float, default=0.0)  # Calculated by ARIA
    
    # Strategic notes (ARIA's observations)
    strategic_notes = Column(Text, nullable=True)  # ARIA's sector analysis
    last_analysis = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    player = relationship("Player", back_populates="aria_exploration_map")
    sector = relationship("Sector")
    
    __table_args__ = (
        Index("idx_aria_exploration_player_sector", "player_id", "sector_id"),
        UniqueConstraint("player_id", "sector_id", name="uq_player_sector_exploration"),
    )


class ARIATradingPattern(Base):
    """
    Learned trading patterns unique to each player
    This is their personal 'Trade DNA' that evolves
    """
    __tablename__ = "aria_trading_patterns"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    pattern_id = Column(String(100), nullable=False)  # Unique pattern identifier
    
    # Pattern definition
    pattern_type = Column(String(50), nullable=False)  # arbitrage, bulk_trade, speculation
    pattern_dna = Column(JSON, nullable=False)  # The actual pattern genes
    
    # Evolution tracking
    generation = Column(Integer, default=1)
    parent_pattern = Column(String(100), nullable=True)  # Parent pattern ID
    mutations = Column(JSON, default=list)  # List of mutations
    
    # Performance metrics
    times_used = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    average_profit = Column(Float, default=0.0)
    best_profit = Column(Float, default=0.0)
    worst_loss = Column(Float, default=0.0)
    
    # Fitness scoring
    fitness_score = Column(Float, default=0.5)  # 0-1, evolutionary fitness
    survival_probability = Column(Float, default=0.5)  # Chance of passing to next gen
    
    # Metadata
    discovered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_used = Column(DateTime(timezone=True), nullable=True)
    evolved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    player = relationship("Player", back_populates="aria_trading_patterns")
    
    __table_args__ = (
        Index("idx_aria_pattern_player_fitness", "player_id", "fitness_score"),
        UniqueConstraint("player_id", "pattern_id", name="uq_player_pattern"),
    )


class ARIAQuantumCache(Base):
    """
    Cache for quantum trade calculations
    Stores ghost trade results to prevent recalculation
    Auto-expires based on market volatility
    """
    __tablename__ = "aria_quantum_cache"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    
    # Cache key components
    cache_key = Column(String(255), nullable=False)  # Hash of trade parameters
    commodity = Column(String(50), nullable=False)
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id"), nullable=True)
    # Nullable as of WO-ARIA-OBS-LOG (migration eb772a1ab433): ADR-0038
    # repurposes this table as the recommendation-aggregate cache
    # (aria_personal_intelligence_service.py's OBSERVATION LOG section),
    # whose per-player bundle has no single-sector scope. Original
    # ghost-trade cache rows keep supplying a real sector_id unchanged.
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id"), nullable=True)
    
    # Cached results
    quantum_states = Column(JSON, nullable=False)  # Superposition states
    ghost_results = Column(JSON, nullable=False)  # Ghost trade outcomes
    expected_value = Column(Float, nullable=False)  # Expected profit/loss
    confidence_interval = Column(JSON, nullable=False)  # [low, high]
    
    # Cache metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    hit_count = Column(Integer, default=0)
    
    # Relationships
    player = relationship("Player")
    station = relationship("Station")
    sector = relationship("Sector")
    
    __table_args__ = (
        Index("idx_quantum_cache_player_key", "player_id", "cache_key"),
        Index("idx_quantum_cache_expiry", "expires_at"),
    )


class ARIASecurityLog(Base):
    """
    Security audit log for ARIA operations
    OWASP compliance: comprehensive logging of all AI decisions
    """
    __tablename__ = "aria_security_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    
    # Event details
    event_type = Column(String(50), nullable=False)  # prediction, trade, warning, manipulation
    event_severity = Column(String(20), nullable=False)  # info, warning, critical
    event_data = Column(JSON, nullable=False)  # Event specifics
    
    # Security tracking
    ip_address = Column(String(45), nullable=True)  # Player's IP
    user_agent = Column(String(255), nullable=True)
    session_id = Column(String(100), nullable=True)
    
    # Threat detection
    anomaly_score = Column(Float, default=0.0)  # 0-1, how unusual this event is
    manipulation_indicators = Column(JSON, default=list)  # Signs of market manipulation
    security_flags = Column(JSON, default=list)  # Any security concerns
    
    # Response
    action_taken = Column(String(100), nullable=True)  # block, warn, allow
    notification_sent = Column(Boolean, default=False)
    
    # Timestamp
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    
    # Relationships
    player = relationship("Player")
    
    __table_args__ = (
        Index("idx_aria_security_player_time", "player_id", "created_at"),
        Index("idx_aria_security_severity", "event_severity"),
        Index("idx_aria_security_anomaly", "anomaly_score"),
    )


class ObservationAction(enum.Enum):
    """Trade leg this observation records. Matches the codebase's
    Python-enum-NAME-as-Postgres-value convention (see market_transaction.py
    TransactionType)."""
    buy = "buy"
    sell = "sell"


class ObservationOutcome(enum.Enum):
    """Sell-leg outcome bucket. NULL on buy-leg rows (profit isn't known
    until the position closes)."""
    profit = "profit"
    break_even = "break_even"
    loss = "loss"


class ARIATradingObservation(Base):
    """
    Per-trade observation log — the substrate for ARIA's SQL-aggregate
    recommendation engine (ADR-0038, OPERATIONS/aria.md § Recommendation
    engine). Append-only: a trade reversal fires a follow-up reversal
    observation rather than editing an existing row, so the log stays an
    auditable historical truth. Replaces the retired ARIATradingPattern
    genetic-algorithm framing — no fitness score, no mutation, no lineage.

    ``source_sector_id`` / ``dest_sector_id`` are the human-readable Integer
    sector numbers (mirrors ``MarketTransaction.sector_id``), NOT a foreign
    key to ``sectors.id`` (which is UUID) — same denormalized-integer
    convention as the sibling transaction table.
    """
    __tablename__ = "aria_trading_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)

    # The underlying trading-service event, when one materialized at insert
    # time. Nullable: this WO leaves the trading.py insert hook (lane C)
    # deferred/unwired, so a defensive nullable FK avoids over-committing to
    # an insert-time guarantee no caller exists to satisfy yet.
    trade_id = Column(UUID(as_uuid=True), ForeignKey("enhanced_market_transactions.id"), nullable=True)

    commodity = Column(String(50), nullable=False)
    action = Column(SQLEnum(ObservationAction), nullable=False)

    source_station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id"), nullable=False)
    dest_station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id"), nullable=True)  # nullable for buy-only events
    source_sector_id = Column(Integer, nullable=True)
    dest_sector_id = Column(Integer, nullable=True)  # nullable for buy-only events

    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer, nullable=False)
    total_credits = Column(Integer, nullable=False)

    # Sell-leg only; NULL on buy-leg rows.
    profit = Column(Integer, nullable=True)
    hours_held = Column(Float, nullable=True)
    outcome_classification = Column(SQLEnum(ObservationOutcome), nullable=True)

    observed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)

    matched_market_intel_id = Column(UUID(as_uuid=True), ForeignKey("aria_market_intelligence.id"), nullable=True)
    # Populated when this trade fulfilled a prior ARIA recommendation.
    # FKs the existing AIRecommendation table (ai_trading.py) — the only
    # "ARIA recommendation" row type in the schema today.
    recommendation_id = Column(UUID(as_uuid=True), ForeignKey("ai_recommendations.id"), nullable=True)

    # Relationships
    player = relationship("Player", back_populates="aria_trading_observations")
    trade = relationship("MarketTransaction")
    source_station = relationship("Station", foreign_keys=[source_station_id])
    dest_station = relationship("Station", foreign_keys=[dest_station_id])
    matched_market_intel = relationship("ARIAMarketIntelligence")
    recommendation = relationship("AIRecommendation")

    __table_args__ = (
        Index("idx_aria_obs_player_commodity", "player_id", "commodity"),
        Index("idx_aria_obs_player_observed_at", "player_id", "observed_at"),
        # Covers the top-routes GROUP BY (commodity, source_station_id,
        # dest_station_id) aggregate — OPERATIONS/aria.md:210.
        Index(
            "idx_aria_obs_player_route",
            "player_id", "commodity", "source_station_id", "dest_station_id",
        ),
    )
