"""Owner-only profession training queue (professions.md § Training mechanism)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class ProfessionTrainingStatus(str, enum.Enum):
    QUEUED = "queued"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProfessionTrainingQueue(Base):
    """One in-flight training order converting generic colonists to specialists."""

    __tablename__ = "profession_training_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    planet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("planets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profession = Column(String(40), nullable=False)
    trainee_count = Column(Integer, nullable=False)
    queued_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    completes_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), nullable=False, default=ProfessionTrainingStatus.QUEUED.value)
