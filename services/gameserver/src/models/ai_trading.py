import uuid
from datetime import datetime
from typing import Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, ForeignKey, func, Numeric, Date, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class AIMarketPrediction(Base):
    """AI-generated market price predictions with confidence intervals"""
    __tablename__ = "ai_market_predictions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    commodity_id = Column(UUID(as_uuid=True), nullable=False)  # References commodities when that table exists
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False)
    predicted_price = Column(Numeric(10, 2), nullable=False)
    confidence_interval = Column(Numeric(3, 2), nullable=False)  # 0.0 to 1.0
    prediction_horizon = Column(Integer, nullable=False)  # hours ahead
    model_version = Column(String(50), nullable=False)
    training_data_points = Column(Integer, nullable=False)
    prediction_factors = Column(JSONB, nullable=True)  # Factors that influenced the prediction
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    actual_price = Column(Numeric(10, 2), nullable=True)  # For accuracy tracking after the fact
    accuracy_score = Column(Numeric(3, 2), nullable=True)  # How accurate was this prediction
    
    # Relationships
    sector = relationship("Sector")

    def __repr__(self):
        return f"<AIMarketPrediction {self.id} - Commodity: {self.commodity_id}, Sector: {self.sector_id}>"

    @property
    def is_expired(self) -> bool:
        """Check if this prediction has expired"""
        return datetime.utcnow() > self.expires_at

    @property
    def confidence_percentage(self) -> float:
        """Return confidence as a percentage"""
        return float(self.confidence_interval) * 100


class PlayerTradingProfile(Base):
    """AI-learned player trading patterns and preferences"""
    __tablename__ = "player_trading_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True)
    risk_tolerance = Column(Numeric(3, 2), nullable=False, default=0.5)  # 0.0 = conservative, 1.0 = aggressive
    preferred_commodities = Column(JSONB, nullable=True)  # List of commodity IDs and preference scores
    avoided_sectors = Column(JSONB, nullable=True)  # Sectors the player tends to avoid
    trading_patterns = Column(JSONB, nullable=True)  # Learned behavioral patterns
    performance_metrics = Column(JSONB, nullable=True)  # Historical performance data
    # 4-level canon vocab (ratified 2026-08-04, ADR-0068): 'minimal', 'quiet',
    # 'standard', 'full' -- was 3-value 'minimal'/'medium'/'full'; 'standard'
    # replaces 'medium' as the default. Legacy rows backfilled by migration.
    ai_assistance_level = Column(String(20), nullable=False, default='standard')
    notification_preferences = Column(JSONB, nullable=True)  # When and how to notify
    learning_data = Column(JSONB, nullable=True)  # ML model training data specific to this player
    last_active_sector = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True)
    average_profit_per_trade = Column(Numeric(10, 2), nullable=False, default=0)
    total_trades_analyzed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    player = relationship("Player", back_populates="trading_profile")
    last_sector = relationship("Sector")

    def __repr__(self):
        return f"<PlayerTradingProfile {self.id} - Player: {self.player_id}>"

    @property
    def risk_level_text(self) -> str:
        """Convert numeric risk tolerance to text description"""
        risk = float(self.risk_tolerance)
        if risk < 0.3:
            return "Conservative"
        elif risk < 0.7:
            return "Moderate" 
        else:
            return "Aggressive"

    def update_performance_metrics(self, new_trade_data: Dict[str, Any]):
        """Update performance metrics with new trade data"""
        if not self.performance_metrics:
            self.performance_metrics = {}
        
        # Update metrics logic would go here
        self.total_trades_analyzed += 1
        self.updated_at = datetime.utcnow()


class AIRecommendation(Base):
    """AI-generated trading recommendations for players"""
    __tablename__ = "ai_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    recommendation_type = Column(String(50), nullable=False)  # 'buy', 'sell', 'route', 'avoid'
    recommendation_data = Column(JSONB, nullable=False)  # Structured recommendation details
    confidence_score = Column(Numeric(3, 2), nullable=False)  # 0.0 to 1.0
    expected_profit = Column(Numeric(10, 2), nullable=True)
    risk_assessment = Column(String(20), nullable=False)  # 'low', 'medium', 'high'
    reasoning = Column(Text, nullable=True)  # Human-readable explanation
    priority_level = Column(Integer, nullable=False, default=3)  # 1-5 scale (5 = urgent)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted = Column(Boolean, nullable=True)  # NULL = pending, True/False = user decision
    acceptance_timestamp = Column(DateTime(timezone=True), nullable=True)
    outcome_profit = Column(Numeric(10, 2), nullable=True)  # Actual profit if recommendation was followed
    outcome_timestamp = Column(DateTime(timezone=True), nullable=True)
    feedback_score = Column(Integer, nullable=True)  # 1-5 user feedback rating
    feedback_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    player = relationship("Player", back_populates="ai_recommendations")

    def __repr__(self):
        return f"<AIRecommendation {self.id} - {self.recommendation_type} for Player: {self.player_id}>"

    @property
    def is_expired(self) -> bool:
        """Check if this recommendation has expired"""
        return datetime.utcnow() > self.expires_at

    @property
    def is_pending(self) -> bool:
        """Check if this recommendation is still awaiting user decision"""
        return self.accepted is None and not self.is_expired

    @property
    def confidence_percentage(self) -> float:
        """Return confidence as a percentage"""
        return float(self.confidence_score) * 100

    @property
    def priority_text(self) -> str:
        """Convert numeric priority to text description"""
        priority_map = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High", 5: "Urgent"}
        return priority_map.get(self.priority_level, "Unknown")

    def calculate_accuracy(self) -> Optional[float]:
        """Calculate recommendation accuracy if outcome data is available"""
        if self.expected_profit is None or self.outcome_profit is None:
            return None
        
        if self.expected_profit == 0:
            return 1.0 if self.outcome_profit >= 0 else 0.0
        
        # Calculate accuracy as percentage of expected vs actual profit
        return max(0.0, min(1.0, float(self.outcome_profit) / float(self.expected_profit)))


class AIModelPerformance(Base):
    """Track performance metrics for AI models"""
    __tablename__ = "ai_model_performance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    model_name = Column(String(100), nullable=False)
    model_version = Column(String(50), nullable=False)
    performance_date = Column(Date, nullable=False)
    total_predictions = Column(Integer, nullable=False)
    correct_predictions = Column(Integer, nullable=False)
    accuracy_percentage = Column(Numeric(5, 2), nullable=False)
    average_confidence = Column(Numeric(3, 2), nullable=False)
    user_acceptance_rate = Column(Numeric(3, 2), nullable=False)
    average_user_satisfaction = Column(Numeric(3, 2), nullable=True)
    profit_improvement_rate = Column(Numeric(5, 2), nullable=True)  # How much AI improves user profits
    performance_metrics = Column(JSONB, nullable=True)  # Additional detailed metrics
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<AIModelPerformance {self.model_name} v{self.model_version} - {self.performance_date}>"

    @property
    def accuracy_description(self) -> str:
        """Return human-readable accuracy description"""
        acc = float(self.accuracy_percentage)
        if acc >= 90:
            return "Excellent"
        elif acc >= 80:
            return "Very Good"
        elif acc >= 70:
            return "Good"
        elif acc >= 60:
            return "Fair"
        else:
            return "Poor"


class AITrainingData(Base):
    """Store market data for AI model training"""
    __tablename__ = "ai_training_data"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    data_type = Column(String(50), nullable=False)  # 'market_price', 'trade_volume', 'player_behavior'
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True)
    commodity_id = Column(UUID(as_uuid=True), nullable=True)  # References commodities when that table exists
    timestamp = Column(DateTime(timezone=True), nullable=False)
    data_value = Column(Numeric(12, 4), nullable=False)
    contextual_data = Column(JSONB, nullable=True)  # Additional context like market conditions
    quality_score = Column(Numeric(3, 2), nullable=False, default=1.0)  # Data quality for training (0.0-1.0)
    used_in_training = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    sector = relationship("Sector")

    def __repr__(self):
        return f"<AITrainingData {self.id} - {self.data_type}: {self.data_value}>"

    @property
    def is_high_quality(self) -> bool:
        """Check if this data point is high quality for training"""
        return float(self.quality_score) >= 0.8

    @property
    def age_hours(self) -> float:
        """Calculate age of this data point in hours"""
        return (datetime.utcnow() - self.timestamp).total_seconds() / 3600