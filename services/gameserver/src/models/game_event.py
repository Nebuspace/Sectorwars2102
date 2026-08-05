import uuid
from sqlalchemy import Column, DateTime, String, Integer, Float, ForeignKey, Text, Boolean, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy import func
import enum

from src.core.database import Base


class EventType(enum.Enum):
    ECONOMIC = "economic"
    COMBAT = "combat"
    EXPLORATION = "exploration"
    SEASONAL = "seasonal"
    EMERGENCY = "emergency"
    STORY = "story"


class EventStatus(enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class EventEffect(Base):
    __tablename__ = "event_effects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"), nullable=False)
    
    # Effect configuration
    effect_type = Column(String(50), nullable=False)  # price_modifier, spawn_rate, experience_bonus, etc.
    target = Column(String(100), nullable=False)      # what is affected (commodity, ship_type, etc.)
    modifier = Column(Float, nullable=False, default=1.0)  # multiplier or flat value
    
    # Scope and duration
    duration_hours = Column(Integer, nullable=True)   # null for permanent effects
    sector_ids = Column(ARRAY(Integer), nullable=True)  # affected sectors, null for global
    
    # Effect metadata
    description = Column(Text, nullable=True)
    effect_data = Column(JSONB, nullable=True)  # additional effect parameters
    
    # Status
    is_active = Column(Boolean, nullable=False, default=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationship
    event = relationship("GameEvent", back_populates="effects")


class GameEvent(Base):
    __tablename__ = "game_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Basic event information
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    event_type = Column(SQLEnum(EventType), nullable=False)
    status = Column(SQLEnum(EventStatus), nullable=False, default=EventStatus.SCHEDULED)
    
    # Timing
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    actual_start_time = Column(DateTime(timezone=True), nullable=True)
    actual_end_time = Column(DateTime(timezone=True), nullable=True)
    
    # Scope and targeting
    affected_regions = Column(ARRAY(String), nullable=True)  # region names/IDs (central-nexus, terran-space, player-owned regions)
    affected_sectors = Column(ARRAY(Integer), nullable=True)  # specific sector IDs
    global_event = Column(Boolean, nullable=False, default=False)
    
    # Participation and rewards
    participation_requirements = Column(JSONB, nullable=True)  # requirements to participate
    rewards = Column(JSONB, nullable=True)  # event rewards structure
    max_participants = Column(Integer, nullable=True)  # participation limit
    
    # Event metrics
    participation_count = Column(Integer, nullable=False, default=0)
    rewards_distributed = Column(Integer, nullable=False, default=0)  # total reward value
    completion_rate = Column(Float, nullable=False, default=0.0)  # percentage of objectives completed
    
    # Administration
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    
    # Event configuration
    auto_start = Column(Boolean, nullable=False, default=True)
    auto_end = Column(Boolean, nullable=False, default=True)
    repeatable = Column(Boolean, nullable=False, default=False)
    priority = Column(Integer, nullable=False, default=1)  # 1=low, 5=critical
    
    # Admin notes and flags
    admin_notes = Column(Text, nullable=True)
    requires_approval = Column(Boolean, nullable=False, default=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    effects = relationship("EventEffect", back_populates="event", cascade="all, delete-orphan")
    participations = relationship("EventParticipation", back_populates="event")
    creator = relationship("User", foreign_keys=[created_by])
    approver = relationship("User", foreign_keys=[approved_by])


class EventTemplate(Base):
    __tablename__ = "event_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Template information
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    event_type = Column(SQLEnum(EventType), nullable=False)
    
    # Default configuration
    default_duration_hours = Column(Integer, nullable=False, default=24)
    default_effects = Column(JSONB, nullable=False)  # default effect configurations
    default_rewards = Column(JSONB, nullable=True)
    
    # Template metadata
    usage_count = Column(Integer, nullable=False, default=0)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    
    # Template status
    is_active = Column(Boolean, nullable=False, default=True)
    is_system_template = Column(Boolean, nullable=False, default=False)  # built-in templates
    
    # Relationships
    creator = relationship("User", foreign_keys=[created_by])


class EventParticipation(Base):
    __tablename__ = "event_participations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Participation details
    event_id = Column(UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    
    # Participation tracking
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_activity = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Progress and rewards
    objectives_completed = Column(Integer, nullable=False, default=0)
    total_objectives = Column(Integer, nullable=False, default=1)
    progress_data = Column(JSONB, nullable=True)  # detailed progress tracking
    
    # Rewards received
    rewards_earned = Column(JSONB, nullable=True)  # rewards the player earned
    rewards_claimed = Column(Boolean, nullable=False, default=False)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Performance metrics
    score = Column(Integer, nullable=False, default=0)
    rank = Column(Integer, nullable=True)  # ranking among participants
    
    # Relationships
    event = relationship("GameEvent", back_populates="participations")
    player = relationship("Player")


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Event tracking
    event_id = Column(UUID(as_uuid=True), ForeignKey("game_events.id", ondelete="CASCADE"), nullable=False)
    
    # Log details
    action = Column(String(100), nullable=False)  # created, started, ended, effect_applied, etc.
    details = Column(Text, nullable=True)
    log_metadata = Column(JSONB, nullable=True)  # additional context data
    
    # Actor information
    actor_type = Column(String(20), nullable=False)  # system, admin, player
    actor_id = Column(UUID(as_uuid=True), nullable=True)  # admin or player ID
    
    # Timestamp
    logged_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    event = relationship("GameEvent")
