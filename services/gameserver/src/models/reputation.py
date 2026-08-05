import uuid
import enum
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class ReputationLevel(enum.Enum):
    PUBLIC_ENEMY = "PUBLIC_ENEMY"
    CRIMINAL = "CRIMINAL"
    OUTLAW = "OUTLAW"
    PIRATE = "PIRATE"
    SMUGGLER = "SMUGGLER"
    UNTRUSTWORTHY = "UNTRUSTWORTHY"
    SUSPICIOUS = "SUSPICIOUS"
    QUESTIONABLE = "QUESTIONABLE"
    NEUTRAL = "NEUTRAL"
    RECOGNIZED = "RECOGNIZED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    TRUSTED = "TRUSTED"
    RESPECTED = "RESPECTED"
    VALUED = "VALUED"
    HONORED = "HONORED"
    REVERED = "REVERED"
    EXALTED = "EXALTED"


class Reputation(Base):
    __tablename__ = "reputations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    faction_id = Column(UUID(as_uuid=True), ForeignKey("factions.id", ondelete="CASCADE"), nullable=False)
    current_value = Column(Integer, nullable=False, default=0)
    current_level = Column(Enum(ReputationLevel, name="reputation_level"), nullable=False, default=ReputationLevel.NEUTRAL)
    title = Column(String(50), nullable=False, default="Neutral")
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decay_paused = Column(Boolean, nullable=False, default=False)
    history = Column(JSONB, nullable=False, default=[])
    
    # Reputation effects
    trade_modifier = Column(Float, nullable=False, default=0)
    mission_availability = Column(JSONB, nullable=False, default=[])
    port_access_level = Column(Integer, nullable=False, default=0)
    combat_response = Column(String(50), nullable=False, default="neutral")
    
    # Special flags
    is_locked = Column(Boolean, nullable=False, default=False)
    lock_reason = Column(String(255), nullable=True)
    lock_expires = Column(DateTime(timezone=True), nullable=True)
    special_status = Column(String(50), nullable=True)

    # Relationships
    player = relationship("Player", back_populates="faction_reputations")
    faction = relationship("Faction", back_populates="reputation_records")

    __table_args__ = (
        # Ensure player can only have one reputation per faction
        {'sqlite_autoincrement': True},
    )

    def __repr__(self):
        return f"<Reputation {self.player_id} with {self.faction_id}: {self.current_value} ({self.current_level.name})>"

    @property
    def is_friendly(self) -> bool:
        return self.current_value > 0
    
    @property
    def is_hostile(self) -> bool:
        return self.current_value < 0
    
    @property
    def is_neutral(self) -> bool:
        return self.current_value == 0
    
    @property
    def numeric_level(self) -> int:
        """Return reputation level as a numeric value from -8 to +8"""
        level_map = {
            ReputationLevel.PUBLIC_ENEMY: -8,
            ReputationLevel.CRIMINAL: -7,
            ReputationLevel.OUTLAW: -6,
            ReputationLevel.PIRATE: -5,
            ReputationLevel.SMUGGLER: -4,
            ReputationLevel.UNTRUSTWORTHY: -3,
            ReputationLevel.SUSPICIOUS: -2,
            ReputationLevel.QUESTIONABLE: -1,
            ReputationLevel.NEUTRAL: 0,
            ReputationLevel.RECOGNIZED: 1,
            ReputationLevel.ACKNOWLEDGED: 2,
            ReputationLevel.TRUSTED: 3,
            ReputationLevel.RESPECTED: 4,
            ReputationLevel.VALUED: 5,
            ReputationLevel.HONORED: 6,
            ReputationLevel.REVERED: 7,
            ReputationLevel.EXALTED: 8
        }
        return level_map[self.current_level]


class TeamReputation(Base):
    __tablename__ = "team_reputations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True)
    calculation_method = Column(String(20), nullable=False, default="AVERAGE")
    faction_reputation = Column(JSONB, nullable=False, default={})
    history = Column(JSONB, nullable=False, default=[])
    last_recalculated = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    next_recalculation = Column(DateTime(timezone=True), nullable=False)
    pending_notifications = Column(JSONB, nullable=False, default=[])

    # Relationships
    team = relationship("Team", back_populates="reputation")

    def __repr__(self):
        return f"<TeamReputation for Team {self.team_id}>" 