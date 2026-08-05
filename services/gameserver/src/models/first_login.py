import uuid
import enum
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, ARRAY, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

class ShipChoice(enum.Enum):
    SCOUT_SHIP = "SCOUT_SHIP"              # Fast ship with good sensors
    CARGO_HAULER = "CARGO_HAULER"          # Spacious trading vessel
    ESCAPE_POD = "ESCAPE_POD"              # Basic starter ship (always present)
    LIGHT_FREIGHTER = "LIGHT_FREIGHTER"    # Balanced ship option
    DEFENDER = "DEFENDER"                  # Combat-focused ship (rare)
    FAST_COURIER = "FAST_COURIER"          # Speed-focused ship (uncommon)
    COLONY_SHIP = "COLONY_SHIP"            # Specialized colonization vessel (very rare)
    CARRIER = "CARRIER"                    # Heavy combat vessel (ultra rare)


class NegotiationSkillLevel(enum.Enum):
    WEAK = "WEAK"                          # Below threshold performance
    AVERAGE = "AVERAGE"                    # Standard performance
    STRONG = "STRONG"                      # Above threshold performance


class DialogueOutcome(enum.Enum):
    SUCCESS = "SUCCESS"                    # Player gets claimed ship
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"    # Player gets ship with penalty
    FAILURE = "FAILURE"                    # Player gets basic ship (Escape Pod)


class DialogueExchange(Base):
    __tablename__ = "dialogue_exchanges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("first_login_sessions.id", ondelete="CASCADE"), nullable=False)
    sequence_number = Column(Integer, nullable=False)  # Order in conversation
    npc_prompt = Column(String, nullable=False)        # What the guard asked
    player_response = Column(String, nullable=False)   # Player's exact response
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    topic = Column(String, nullable=True)              # Topic category of question

    # AI analysis data (existing)
    persuasiveness = Column(Float, nullable=True)      # 0-1 persuasion score
    confidence = Column(Float, nullable=True)          # 0-1 confidence score
    consistency = Column(Float, nullable=True)         # 0-1 consistency with prior claims
    key_extracted_info = Column(JSONB, nullable=True)  # Key-value pairs of extracted data
    detected_contradictions = Column(ARRAY(String), nullable=True)  # Any contradictions found

    # NEW: AI provider and prompt logging (for admin debugging)
    ai_provider = Column(String(20), nullable=True)    # 'openai', 'anthropic', 'fallback'
    ai_system_prompt = Column(String, nullable=True)   # Full system prompt sent to AI
    ai_user_prompt = Column(String, nullable=True)     # Full user prompt sent to AI
    ai_raw_response = Column(String, nullable=True)    # Raw AI response before parsing

    # NEW: Additional analysis metrics
    believability = Column(Float, nullable=True)       # 0-1 overall believability
    current_suspicion = Column(Float, nullable=True)   # Guard's suspicion at this exchange

    # NEW: Performance & cost tracking
    response_time_ms = Column(Integer, nullable=True)  # AI response time in milliseconds
    estimated_cost_usd = Column(Float, nullable=True)  # Estimated API cost
    tokens_used = Column(Integer, nullable=True)       # Total tokens (input + output)

    # Relationship
    session = relationship("FirstLoginSession", back_populates="dialogue_exchanges")

    def __repr__(self):
        return f"<DialogueExchange {self.id}: Sequence {self.sequence_number}>"


class ShipPresentationOptions(Base):
    __tablename__ = "ship_presentation_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("first_login_sessions.id", ondelete="CASCADE"), nullable=False)
    available_ships = Column(ARRAY(String), nullable=False)  # List of available ship types
    escape_pod_present = Column(Boolean, nullable=False, default=True)
    rarity_roll = Column(Integer, nullable=False)            # 0-100 rarity roll result
    special_event_active = Column(Boolean, nullable=False, default=False)
    seed_value = Column(String, nullable=True)               # Random seed for reproducibility

    # Relationship
    session = relationship("FirstLoginSession", back_populates="ship_options")

    def __repr__(self):
        return f"<ShipPresentationOptions {self.id}: Ships {', '.join(self.available_ships)}>"


class FirstLoginSession(Base):
    __tablename__ = "first_login_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    ai_service_used = Column(Boolean, nullable=False, default=False)
    fallback_to_rules = Column(Boolean, nullable=False, default=False)

    # Guard Personality (generated per session for consistency)
    guard_name = Column(String, nullable=True)
    guard_title = Column(String, nullable=True)
    guard_trait = Column(String, nullable=True)
    guard_base_suspicion = Column(Float, nullable=True)
    guard_description = Column(String, nullable=True)

    # Player Choices
    ship_claimed = Column(Enum(ShipChoice, name="ship_choice"), nullable=True)
    extracted_player_name = Column(String, nullable=True)
    
    # Evaluation Results
    negotiation_skill = Column(Enum(NegotiationSkillLevel, name="negotiation_skill_level"), nullable=True)
    final_persuasion_score = Column(Float, nullable=True)
    outcome = Column(Enum(DialogueOutcome, name="dialogue_outcome"), nullable=True)
    
    # Resulting Game State
    awarded_ship = Column(Enum(ShipChoice, name="awarded_ship_type"), nullable=True)
    starting_credits = Column(Integer, nullable=True)
    negotiation_bonus_flag = Column(Boolean, nullable=True)
    notoriety_penalty = Column(Boolean, nullable=True)
    
    # Technical Metadata
    client_info = Column(JSONB, nullable=True)  # Device, browser info
    performance_metrics = Column(JSONB, nullable=True)  # Response times, latency
    
    # Relationships
    player = relationship("Player", back_populates="first_login_sessions")
    dialogue_exchanges = relationship("DialogueExchange", back_populates="session", cascade="all, delete-orphan")
    ship_options = relationship("ShipPresentationOptions", back_populates="session", uselist=False, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<FirstLoginSession {self.id}: Player {self.player_id}, Completed: {self.completed_at is not None}>"


class ShipRarityConfig(Base):
    __tablename__ = "ship_rarity_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ship_type = Column(Enum(ShipChoice, name="ship_type_config"), nullable=False, unique=True)
    rarity_tier = Column(Integer, nullable=False)  # 1-5 (1=common, 5=extremely rare)
    spawn_chance = Column(Integer, nullable=False)  # 0-100 base chance to appear
    base_credits = Column(Integer, nullable=False)  # Starting credits if awarded
    
    # Persuasion thresholds by negotiation skill
    weak_threshold = Column(Float, nullable=False)
    average_threshold = Column(Float, nullable=False)
    strong_threshold = Column(Float, nullable=False)
    
    def __repr__(self):
        return f"<ShipRarityConfig {self.ship_type.name}: Tier {self.rarity_tier}, Chance {self.spawn_chance}%>"


class PlayerFirstLoginState(Base):
    __tablename__ = "player_first_login_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True)
    has_completed_first_login = Column(Boolean, nullable=False, default=False)
    current_session_id = Column(UUID(as_uuid=True), ForeignKey("first_login_sessions.id"), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    
    # Completion tracking
    claimed_ship = Column(Boolean, nullable=False, default=False)
    answered_questions = Column(Boolean, nullable=False, default=False)
    received_resources = Column(Boolean, nullable=False, default=False)
    tutorial_started = Column(Boolean, nullable=False, default=False)
    
    # Tracking prior choices
    previous_ship_claims = Column(ARRAY(String), nullable=False, default=[])
    previous_dialogue_strategies = Column(ARRAY(String), nullable=False, default=[])
    
    # Relationships
    player = relationship("Player", back_populates="first_login_state")
    current_session = relationship("FirstLoginSession", foreign_keys=[current_session_id])
    
    def __repr__(self):
        return f"<PlayerFirstLoginState {self.id}: Player {self.player_id}, Completed: {self.has_completed_first_login}>"