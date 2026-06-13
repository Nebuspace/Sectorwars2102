"""
Enhanced AI Models - OWASP Security-First Design
Building on existing ARIA foundation with cross-system intelligence

Security Features:
- Input validation and sanitization
- SQL injection prevention via SQLAlchemy ORM
- XSS prevention through length limits and encoding
- Rate limiting and quota management
- Audit logging for all AI operations
- Row-level security compliance
- GDPR-compliant data retention
"""

import uuid
import json
import hashlib
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any, Union, TYPE_CHECKING
from enum import Enum

from sqlalchemy import (
    Boolean, Column, DateTime, String, Integer, Float, ForeignKey, 
    func, Numeric, Date, Text, CheckConstraint, Index, BigInteger,
    UniqueConstraint, event
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import and_, or_

from src.core.database import Base
from src.models.ai_trading import PlayerTradingProfile, AIMarketPrediction, AIRecommendation

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.sector import Sector


class SecurityLevel(str, Enum):
    """Security levels for AI data classification"""
    BASIC = "basic"
    STANDARD = "standard" 
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class SecurityClassification(str, Enum):
    """Data security classification levels"""
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"


class DataSensitivity(str, Enum):
    """Data sensitivity levels for GDPR compliance"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AIComprehensiveAssistant(Base):
    """
    Enhanced AI Assistant with cross-system intelligence and security controls
    Building on existing ARIA foundation
    """
    __tablename__ = "ai_comprehensive_assistants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Assistant configuration
    assistant_name = Column(String(50), nullable=False, default="ARIA")
    personality_type = Column(String(20), nullable=False, default="analytical")
    learning_mode = Column(String(20), nullable=False, default="balanced")
    
    # Security and access control
    security_level = Column(String(20), nullable=False, default=SecurityLevel.STANDARD)
    encryption_key_id = Column(UUID(as_uuid=True), nullable=True)  # External key management
    # ARIA advises across all systems by default (read-only analysis of the
    # player's own holdings); action-taking features still gate elsewhere.
    access_permissions = Column(JSONB, nullable=False, default={"trading": True, "combat": True, "colony": True, "station": True})
    
    # Performance and rate limiting
    api_request_quota = Column(Integer, nullable=False, default=1000)
    api_requests_used = Column(Integer, nullable=False, default=0)
    # Python-side callable default (date.today) — the previous
    # `default=func.current_date` passed a SQL function OBJECT as a bind value,
    # which asyncpg cannot serialize (DataError on every assistant INSERT).
    quota_reset_date = Column(Date, nullable=False, default=date.today)

    # Metadata
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_active = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    total_interactions = Column(BigInteger, nullable=False, default=0)
    learning_sessions = Column(Integer, nullable=False, default=0)
    
    # Relationships
    player = relationship("Player", back_populates="ai_assistant")
    knowledge_base = relationship("AICrossSystemKnowledge", back_populates="assistant", cascade="all, delete-orphan")
    recommendations = relationship("AIStrategicRecommendation", back_populates="assistant", cascade="all, delete-orphan")
    learning_patterns = relationship("AILearningPattern", back_populates="assistant", cascade="all, delete-orphan")
    conversations = relationship("AIConversationLog", back_populates="assistant", cascade="all, delete-orphan")
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            personality_type.in_(["analytical", "friendly", "tactical", "cautious", "adaptive"]),
            name="valid_personality_type"
        ),
        CheckConstraint(
            learning_mode.in_(["conservative", "balanced", "aggressive", "custom"]),
            name="valid_learning_mode"
        ),
        CheckConstraint(
            security_level.in_([level.value for level in SecurityLevel]),
            name="valid_security_level"
        ),
        CheckConstraint(
            and_(api_request_quota >= 100, api_request_quota <= 10000),
            name="quota_bounds"
        ),
        CheckConstraint(
            and_(api_requests_used >= 0, api_requests_used <= api_request_quota),
            name="usage_bounds"
        ),
        CheckConstraint(
            and_(total_interactions >= 0, learning_sessions >= 0),
            name="positive_counters"
        ),
    )

    @validates('assistant_name')
    def validate_assistant_name(self, key, value):
        """Validate and sanitize assistant name"""
        if not value or len(value.strip()) == 0:
            return "ARIA"
        
        # Sanitize: remove HTML tags and dangerous characters
        import re
        value = re.sub(r'<[^>]*>', '', str(value))
        value = re.sub(r'[<>"\'`]', '', value)
        value = value.strip()[:50]  # Limit length
        
        return value or "ARIA"

    @validates('access_permissions')
    def validate_access_permissions(self, key, value):
        """Validate access permissions structure"""
        if not isinstance(value, dict):
            raise ValueError("Access permissions must be a dictionary")
        
        required_keys = {'trading', 'combat', 'colony', 'station'}
        if not required_keys.issubset(value.keys()):
            raise ValueError(f"Access permissions must contain keys: {required_keys}")
        
        # Ensure all values are boolean
        for k, v in value.items():
            if not isinstance(v, bool):
                raise ValueError(f"Permission '{k}' must be boolean")
        
        return value

    @hybrid_property
    def quota_remaining(self) -> int:
        """Calculate remaining API quota"""
        return max(0, self.api_request_quota - self.api_requests_used)

    @hybrid_property
    def is_quota_exceeded(self) -> bool:
        """Check if quota is exceeded"""
        return self.api_requests_used >= self.api_request_quota

    def check_rate_limit(self) -> bool:
        """Check and enforce rate limiting"""
        # Reset quota if new day
        if self.quota_reset_date < date.today():
            self.api_requests_used = 0
            self.quota_reset_date = date.today()
        
        if self.is_quota_exceeded:
            return False
        
        # Increment usage
        self.api_requests_used += 1
        self.last_active = datetime.utcnow()
        return True

    def has_permission(self, system: str) -> bool:
        """Check if assistant has permission for specific system"""
        return self.access_permissions.get(system, False)

    def get_security_clearance_level(self) -> int:
        """Get numeric security clearance level"""
        levels = {
            SecurityLevel.BASIC: 1,
            SecurityLevel.STANDARD: 2,
            SecurityLevel.PREMIUM: 3,
            SecurityLevel.ENTERPRISE: 4
        }
        return levels.get(self.security_level, 1)


class AICrossSystemKnowledge(Base):
    """
    Cross-system AI knowledge with security classification and validation
    Stores knowledge across trading, combat, colonization, port management
    """
    __tablename__ = "ai_cross_system_knowledge"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("ai_comprehensive_assistants.id", ondelete="CASCADE"), nullable=False)
    
    # Knowledge classification
    knowledge_domain = Column(String(30), nullable=False)
    knowledge_type = Column(String(50), nullable=False)
    knowledge_subtype = Column(String(50), nullable=True)
    
    # Security and access
    security_classification = Column(String(20), nullable=False, default=SecurityClassification.INTERNAL)
    data_sensitivity = Column(String(20), nullable=False, default=DataSensitivity.LOW)
    
    # Knowledge data (with encryption for sensitive information)
    knowledge_data = Column(JSONB, nullable=False)
    encrypted_knowledge = Column(BYTEA, nullable=True)  # For highly sensitive data
    
    # Confidence and validation
    confidence_score = Column(Numeric(4, 3), nullable=False, default=0.500)
    validation_count = Column(Integer, nullable=False, default=0)
    accuracy_score = Column(Numeric(4, 3), nullable=True)
    
    # Temporal data
    knowledge_timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expiry_date = Column(DateTime(timezone=True), nullable=True)
    last_validated = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Source tracking for audit
    data_source = Column(String(50), nullable=False, default="player_action")
    source_metadata = Column(JSONB, default={})
    
    # Relationships
    assistant = relationship("AIComprehensiveAssistant", back_populates="knowledge_base")
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            knowledge_domain.in_(["trading", "combat", "colony", "station", "strategic", "social"]),
            name="valid_knowledge_domain"
        ),
        CheckConstraint(
            security_classification.in_([level.value for level in SecurityClassification]),
            name="valid_security_classification"
        ),
        CheckConstraint(
            data_sensitivity.in_([level.value for level in DataSensitivity]),
            name="valid_data_sensitivity"
        ),
        CheckConstraint(
            and_(confidence_score >= 0.0, confidence_score <= 1.0),
            name="valid_confidence_score"
        ),
        CheckConstraint(
            or_(accuracy_score.is_(None), and_(accuracy_score >= 0.0, accuracy_score <= 1.0)),
            name="valid_accuracy_score"
        ),
        CheckConstraint(validation_count >= 0, name="positive_validation_count"),
        Index("idx_knowledge_domain_confidence", knowledge_domain, confidence_score.desc(), created_at.desc()),
        Index("idx_knowledge_security_level", security_classification, data_sensitivity),
    )

    @validates('knowledge_data')
    def validate_knowledge_data(self, key, value):
        """Validate knowledge data structure and size"""
        if not isinstance(value, dict):
            raise ValueError("Knowledge data must be a dictionary")
        
        # Check size limit (64KB)
        json_str = json.dumps(value)
        if len(json_str.encode('utf-8')) > 65536:
            raise ValueError("Knowledge data exceeds 64KB limit")
        
        # Check for dangerous keys
        dangerous_keys = {'__proto__', 'constructor', 'prototype'}
        if any(key in value for key in dangerous_keys):
            raise ValueError("Knowledge data contains dangerous keys")
        
        return value

    @validates('source_metadata')
    def validate_source_metadata(self, key, value):
        """Validate source metadata structure"""
        if value is None:
            return {}
        
        if not isinstance(value, dict):
            raise ValueError("Source metadata must be a dictionary")
        
        # Size limit (4KB)
        json_str = json.dumps(value)
        if len(json_str.encode('utf-8')) > 4096:
            raise ValueError("Source metadata exceeds 4KB limit")
        
        return value

    @hybrid_property
    def is_high_confidence(self) -> bool:
        """Check if knowledge has high confidence"""
        return float(self.confidence_score) >= 0.8

    @hybrid_property
    def is_expired(self) -> bool:
        """Check if knowledge has expired"""
        if self.expiry_date is None:
            return False
        return datetime.utcnow() > self.expiry_date

    def can_access(self, security_clearance: str, required_sensitivity: str = None) -> bool:
        """Check if knowledge can be accessed based on security clearance"""
        clearance_levels = {
            SecurityLevel.BASIC: 1,
            SecurityLevel.STANDARD: 2,
            SecurityLevel.PREMIUM: 3,
            SecurityLevel.ENTERPRISE: 4
        }
        
        classification_levels = {
            SecurityClassification.PUBLIC: 1,
            SecurityClassification.INTERNAL: 2,
            SecurityClassification.RESTRICTED: 3,
            SecurityClassification.CONFIDENTIAL: 4
        }
        
        user_level = clearance_levels.get(security_clearance, 1)
        required_level = classification_levels.get(self.security_classification, 4)
        
        return user_level >= required_level

    def update_confidence(self, new_score: float, validation_result: bool = True):
        """Update confidence score based on validation"""
        self.validation_count += 1
        
        if validation_result:
            # Increase confidence slightly for successful validation
            self.confidence_score = min(1.0, float(self.confidence_score) + 0.05)
        else:
            # Decrease confidence for failed validation
            self.confidence_score = max(0.0, float(self.confidence_score) - 0.1)
        
        self.last_validated = datetime.utcnow()
        self.updated_at = datetime.utcnow()


class AIStrategicRecommendation(Base):
    """
    AI-generated strategic recommendations with enhanced security and outcome tracking
    Extends existing AIRecommendation with cross-system capabilities
    """
    __tablename__ = "ai_strategic_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("ai_comprehensive_assistants.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    
    # Recommendation details
    recommendation_category = Column(String(30), nullable=False)
    recommendation_type = Column(String(50), nullable=False)
    priority_level = Column(Integer, nullable=False, default=3)
    
    # Security and compliance
    security_clearance_required = Column(String(20), nullable=False, default=SecurityLevel.STANDARD)
    compliance_flags = Column(JSONB, default=[])
    
    # Recommendation content
    recommendation_title = Column(String(200), nullable=False)
    recommendation_summary = Column(Text, nullable=False)
    detailed_analysis = Column(JSONB, nullable=False)
    
    # Risk and financial analysis
    risk_assessment = Column(String(20), nullable=False)
    expected_outcome = Column(JSONB, nullable=False)
    confidence_interval = Column(Numeric(4, 3), nullable=False)
    
    # User interaction tracking
    presented_to_user = Column(Boolean, nullable=False, default=False)
    user_response = Column(String(20), nullable=True)
    user_feedback_score = Column(Integer, nullable=True)
    user_feedback_text = Column(Text, nullable=True)
    
    # Outcome tracking
    outcome_tracked = Column(Boolean, nullable=False, default=False)
    actual_outcome = Column(JSONB, nullable=True)
    outcome_accuracy = Column(Numeric(4, 3), nullable=True)
    
    # Temporal data
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    user_responded_at = Column(DateTime(timezone=True), nullable=True)
    outcome_recorded_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    assistant = relationship("AIComprehensiveAssistant", back_populates="recommendations")
    player = relationship("Player")
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            recommendation_category.in_(["trading", "combat", "colony", "station", "strategic", "resource"]),
            name="valid_recommendation_category"
        ),
        CheckConstraint(
            and_(priority_level >= 1, priority_level <= 5),
            name="valid_priority_level"
        ),
        CheckConstraint(
            security_clearance_required.in_([level.value for level in SecurityLevel]),
            name="valid_security_clearance"
        ),
        CheckConstraint(
            risk_assessment.in_(["very_low", "low", "medium", "high", "very_high"]),
            name="valid_risk_assessment"
        ),
        CheckConstraint(
            and_(confidence_interval >= 0.0, confidence_interval <= 1.0),
            name="valid_confidence_interval"
        ),
        CheckConstraint(
            or_(user_response.is_(None), user_response.in_(["accepted", "rejected", "modified", "deferred"])),
            name="valid_user_response"
        ),
        CheckConstraint(
            or_(user_feedback_score.is_(None), and_(user_feedback_score >= 1, user_feedback_score <= 5)),
            name="valid_feedback_score"
        ),
        CheckConstraint(
            or_(outcome_accuracy.is_(None), and_(outcome_accuracy >= 0.0, outcome_accuracy <= 1.0)),
            name="valid_outcome_accuracy"
        ),
        CheckConstraint(expires_at > created_at, name="valid_expiry"),
        CheckConstraint(
            func.length(recommendation_summary) <= 1000,
            name="summary_length_limit"
        ),
        CheckConstraint(
            or_(user_feedback_text.is_(None), func.length(user_feedback_text) <= 2000),
            name="feedback_length_limit"
        ),
        Index("idx_recommendations_active", player_id, recommendation_category, created_at.desc()),
        Index("idx_recommendations_expiry", expires_at),
    )

    @validates('recommendation_summary', 'user_feedback_text')
    def validate_text_fields(self, key, value):
        """Sanitize text fields to prevent XSS"""
        if value is None:
            return None
        
        import re
        # Remove HTML tags and dangerous characters
        value = re.sub(r'<[^>]*>', '', str(value))
        value = re.sub(r'[<>"\'`]', '', value)
        value = value.strip()
        
        # Apply length limits
        if key == 'recommendation_summary' and len(value) > 1000:
            value = value[:1000]
        elif key == 'user_feedback_text' and len(value) > 2000:
            value = value[:2000]
        
        return value

    @validates('detailed_analysis', 'expected_outcome', 'actual_outcome')
    def validate_jsonb_fields(self, key, value):
        """Validate JSONB structure and size"""
        if value is None:
            return None
        
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dictionary")
        
        # Check size limit (32KB for detailed analysis, 16KB for outcomes)
        json_str = json.dumps(value)
        size_limit = 32768 if key == 'detailed_analysis' else 16384
        
        if len(json_str.encode('utf-8')) > size_limit:
            raise ValueError(f"{key} exceeds size limit")
        
        # Ensure expected_outcome has required 'type' field
        if key == 'expected_outcome' and 'type' not in value:
            raise ValueError("Expected outcome must have 'type' field")
        
        return value

    @hybrid_property
    def is_expired(self) -> bool:
        """Check if recommendation has expired"""
        return datetime.utcnow() > self.expires_at

    @hybrid_property
    def is_pending(self) -> bool:
        """Check if recommendation is awaiting user response"""
        return self.user_response is None and not self.is_expired

    @hybrid_property
    def priority_text(self) -> str:
        """Get text description of priority level"""
        levels = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Urgent"}
        return levels.get(self.priority_level, "Unknown")

    def calculate_accuracy(self) -> Optional[float]:
        """Calculate recommendation accuracy based on actual outcome"""
        if not self.actual_outcome or not self.expected_outcome:
            return None
        
        expected_value = self.expected_outcome.get('value', 0)
        actual_value = self.actual_outcome.get('value', 0)
        
        if expected_value == 0:
            return 1.0 if actual_value >= 0 else 0.0
        
        return max(0.0, min(1.0, actual_value / expected_value))

    def record_outcome(self, outcome_data: Dict[str, Any]) -> float:
        """Record actual outcome and calculate accuracy"""
        self.actual_outcome = outcome_data
        self.outcome_recorded_at = datetime.utcnow()
        self.outcome_tracked = True
        
        accuracy = self.calculate_accuracy()
        if accuracy is not None:
            self.outcome_accuracy = accuracy
        
        return accuracy or 0.0


class AILearningPattern(Base):
    """
    AI-discovered patterns with validation and version control
    Tracks learned behavioral and strategic patterns
    """
    __tablename__ = "ai_learning_patterns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("ai_comprehensive_assistants.id", ondelete="CASCADE"), nullable=False)
    
    # Pattern identification
    pattern_category = Column(String(30), nullable=False)
    pattern_name = Column(String(100), nullable=False)
    pattern_version = Column(Integer, nullable=False, default=1)
    
    # Pattern data and analysis
    pattern_description = Column(Text, nullable=False)
    pattern_data = Column(JSONB, nullable=False)
    pattern_conditions = Column(JSONB, nullable=False, default={})
    
    # Validation and performance
    confidence_score = Column(Numeric(4, 3), nullable=False)
    validation_attempts = Column(Integer, nullable=False, default=0)
    successful_validations = Column(Integer, nullable=False, default=0)
    
    # Usage tracking
    application_count = Column(Integer, nullable=False, default=0)
    last_applied = Column(DateTime(timezone=True), nullable=True)
    
    # Security and lifecycle
    security_classification = Column(String(20), nullable=False, default=SecurityClassification.INTERNAL)
    is_active = Column(Boolean, nullable=False, default=True)
    deactivated_reason = Column(Text, nullable=True)
    
    # Temporal data
    discovered_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_validated = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Relationships
    assistant = relationship("AIComprehensiveAssistant", back_populates="learning_patterns")
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            pattern_category.in_(["behavioral", "market", "tactical", "strategic", "social"]),
            name="valid_pattern_category"
        ),
        CheckConstraint(pattern_version > 0, name="positive_pattern_version"),
        CheckConstraint(
            and_(confidence_score >= 0.0, confidence_score <= 1.0),
            name="valid_confidence_score"
        ),
        CheckConstraint(validation_attempts >= 0, name="positive_validation_attempts"),
        CheckConstraint(
            successful_validations <= validation_attempts,
            name="success_validation_bounds"
        ),
        CheckConstraint(application_count >= 0, name="positive_application_count"),
        CheckConstraint(
            security_classification.in_([level.value for level in SecurityClassification]),
            name="valid_security_classification"
        ),
        CheckConstraint(
            func.length(pattern_description) <= 2000,
            name="description_length_limit"
        ),
        UniqueConstraint("assistant_id", "pattern_name", "pattern_version", name="unique_pattern_version"),
        Index("idx_patterns_active", assistant_id, pattern_category),
    )

    @hybrid_property 
    def success_rate(self) -> Optional[float]:
        """Calculate pattern success rate"""
        if self.validation_attempts == 0:
            return None
        return self.successful_validations / self.validation_attempts

    @validates('pattern_description')
    def validate_pattern_description(self, key, value):
        """Sanitize pattern description"""
        import re
        value = re.sub(r'<[^>]*>', '', str(value))
        value = re.sub(r'[<>"\'`]', '', value)
        return value.strip()[:2000]

    def validate_pattern(self, validation_result: bool):
        """Record pattern validation result"""
        self.validation_attempts += 1
        if validation_result:
            self.successful_validations += 1
            # Increase confidence for successful validation
            self.confidence_score = min(1.0, float(self.confidence_score) + 0.02)
        else:
            # Decrease confidence for failed validation
            self.confidence_score = max(0.0, float(self.confidence_score) - 0.05)
        
        self.last_validated = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def apply_pattern(self):
        """Record pattern application"""
        self.application_count += 1
        self.last_applied = datetime.utcnow()


class AIConversationLog(Base):
    """
    Secure conversation history with privacy controls and data retention
    GDPR-compliant conversation logging
    """
    __tablename__ = "ai_conversation_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("ai_comprehensive_assistants.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    
    # Conversation metadata
    conversation_type = Column(String(30), nullable=False)
    interaction_sequence = Column(Integer, nullable=False)
    
    # Privacy and security
    privacy_level = Column(String(20), nullable=False, default="standard")
    data_retention_days = Column(Integer, nullable=False, default=365)
    
    # Input processing (sanitized and validated)
    user_input_sanitized = Column(Text, nullable=False)
    input_intent = Column(String(50), nullable=True)
    input_confidence = Column(Numeric(4, 3), nullable=True)
    
    # AI response
    ai_response_text = Column(Text, nullable=False)
    response_type = Column(String(30), nullable=False)
    response_confidence = Column(Numeric(4, 3), nullable=False)
    response_time_ms = Column(Integer, nullable=False)
    
    # Context and state
    conversation_context = Column(JSONB, default={})
    ai_state_snapshot = Column(JSONB, default={})
    
    # User feedback and learning
    user_satisfaction = Column(Integer, nullable=True)
    follow_up_action_taken = Column(Boolean, nullable=True)
    learning_value = Column(String(20), nullable=True)
    
    # Temporal and audit data
    interaction_timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip_address_hash = Column(String(64), nullable=True)  # Hashed for privacy
    user_agent_hash = Column(String(64), nullable=True)  # Hashed for privacy
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Relationships
    assistant = relationship("AIComprehensiveAssistant", back_populates="conversations")
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            conversation_type.in_(["query", "command", "feedback", "learning", "strategic"]),
            name="valid_conversation_type"
        ),
        CheckConstraint(interaction_sequence > 0, name="positive_interaction_sequence"),
        CheckConstraint(
            privacy_level.in_(["public", "standard", "private", "confidential"]),
            name="valid_privacy_level"
        ),
        CheckConstraint(
            and_(data_retention_days >= 30, data_retention_days <= 2555),
            name="valid_retention_days"
        ),
        CheckConstraint(
            response_type.in_(["answer", "question", "recommendation", "error", "clarification"]),
            name="valid_response_type"
        ),
        CheckConstraint(
            or_(input_confidence.is_(None), and_(input_confidence >= 0.0, input_confidence <= 1.0)),
            name="valid_input_confidence"
        ),
        CheckConstraint(
            and_(response_confidence >= 0.0, response_confidence <= 1.0),
            name="valid_response_confidence"
        ),
        CheckConstraint(response_time_ms > 0, name="positive_response_time"),
        CheckConstraint(
            or_(user_satisfaction.is_(None), and_(user_satisfaction >= 1, user_satisfaction <= 5)),
            name="valid_user_satisfaction"
        ),
        CheckConstraint(
            or_(learning_value.is_(None), learning_value.in_(["none", "low", "medium", "high", "critical"])),
            name="valid_learning_value"
        ),
        CheckConstraint(
            func.length(user_input_sanitized) <= 4000,
            name="input_length_limit"
        ),
        CheckConstraint(
            func.length(ai_response_text) <= 8000,
            name="response_length_limit"
        ),
        Index("idx_conversations_session", session_id, interaction_sequence),
        Index("idx_conversations_retention", interaction_timestamp),
    )

    @hybrid_property
    def expires_at(self) -> datetime:
        """Calculate expiration date based on retention policy"""
        return self.created_at + timedelta(days=self.data_retention_days)

    @hybrid_property
    def is_expired(self) -> bool:
        """Check if conversation log has expired"""
        return datetime.utcnow() > self.expires_at

    @validates('user_input_sanitized', 'ai_response_text')
    def sanitize_text_content(self, key, value):
        """Sanitize conversation text to prevent XSS"""
        import re
        # Remove HTML tags and dangerous characters
        value = re.sub(r'<[^>]*>', '', str(value))
        value = re.sub(r'[<>"\'`]', '', value)
        value = value.strip()
        
        # Apply length limits
        if key == 'user_input_sanitized' and len(value) > 4000:
            value = value[:4000]
        elif key == 'ai_response_text' and len(value) > 8000:
            value = value[:8000]
        
        return value

    @staticmethod
    def hash_sensitive_data(data: str) -> str:
        """Hash sensitive data like IP addresses"""
        if not data:
            return None
        return hashlib.sha256(data.encode('utf-8')).hexdigest()


class AISecurityAuditLog(Base):
    """
    Security audit trail for AI operations and compliance monitoring
    OWASP compliance and security event tracking
    """
    __tablename__ = "ai_security_audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("ai_comprehensive_assistants.id", ondelete="SET NULL"), nullable=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    
    # Event classification
    event_type = Column(String(50), nullable=False)
    severity_level = Column(String(20), nullable=False)
    
    # Event details
    event_description = Column(Text, nullable=False)
    event_data = Column(JSONB, default={})
    
    # Security context
    security_context = Column(JSONB, nullable=False, default={})
    ip_address_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Detection and response
    detection_method = Column(String(50), nullable=True)
    automated_response = Column(String(100), nullable=True)
    requires_investigation = Column(Boolean, nullable=False, default=False)
    investigation_status = Column(String(20), nullable=True)
    
    # Temporal data
    event_timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            event_type.in_(["access", "data_access", "recommendation", "pattern_learning", "security_violation", "quota_exceeded"]),
            name="valid_event_type"
        ),
        CheckConstraint(
            severity_level.in_(["info", "warning", "error", "critical"]),
            name="valid_severity_level"
        ),
        CheckConstraint(
            or_(investigation_status.is_(None), investigation_status.in_(["pending", "in_progress", "resolved", "false_positive"])),
            name="valid_investigation_status"
        ),
        CheckConstraint(
            func.length(event_description) <= 1000,
            name="description_length_limit"
        ),
        Index("idx_audit_security_events", event_type, severity_level, event_timestamp.desc()),
        Index("idx_audit_investigation", requires_investigation, investigation_status, created_at.desc()),
    )

    @classmethod
    def log_event(cls, event_type: str, severity: str, description: str, 
                 assistant_id: UUID = None, player_id: UUID = None, 
                 event_data: Dict = None, security_context: Dict = None):
        """Convenience method to log security events"""
        return cls(
            event_type=event_type,
            severity_level=severity,
            event_description=description[:1000],  # Truncate if needed
            assistant_id=assistant_id,
            player_id=player_id,
            event_data=event_data or {},
            security_context=security_context or {"source": "system"}
        )


# Event listeners for automatic security logging
@event.listens_for(AICrossSystemKnowledge, 'after_insert')
def log_sensitive_knowledge_creation(mapper, connection, target):
    """Log creation of sensitive AI knowledge"""
    if target.security_classification in ['restricted', 'confidential'] or target.data_sensitivity in ['high', 'critical']:
        # Note: In production, this would use a separate database session
        # to avoid issues with the current transaction
        pass  # Security logging would be implemented here


# Add relationships to Player model (to be added to player.py)
# player.ai_assistant = relationship("AIComprehensiveAssistant", back_populates="player", uselist=False)
# player.trading_profile = relationship("PlayerTradingProfile", back_populates="player", uselist=False)
# player.ai_recommendations = relationship("AIRecommendation", back_populates="player")