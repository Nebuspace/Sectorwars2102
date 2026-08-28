"""Colonist profession aggregates per planet (FEATURES/planets/professions.md)."""

import enum
import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class ProfessionType(str, enum.Enum):
    """Twelve canon colonist professions (professions.md § The 12 professions)."""

    SPACE_ENGINEERS = "SPACE_ENGINEERS"
    STRUCTURAL_ENGINEERS = "STRUCTURAL_ENGINEERS"
    MINING_ENGINEERS = "MINING_ENGINEERS"
    RESEARCH_SCIENTISTS = "RESEARCH_SCIENTISTS"
    AGRICULTURAL_SCIENTISTS = "AGRICULTURAL_SCIENTISTS"
    MEDICAL_PROFESSIONALS = "MEDICAL_PROFESSIONALS"
    TERRAFORM_ENGINEERS = "TERRAFORM_ENGINEERS"
    COMBAT_PILOTS = "COMBAT_PILOTS"
    DEFENSE_COORDINATORS = "DEFENSE_COORDINATORS"
    STRATEGIC_ANALYSTS = "STRATEGIC_ANALYSTS"
    TRADE_SPECIALISTS = "TRADE_SPECIALISTS"
    INDUSTRIAL_MANAGERS = "INDUSTRIAL_MANAGERS"


# Real-time training durations in days (professions.md table — costs TBD, not stored here).
PROFESSION_TRAINING_DAYS: dict[ProfessionType, int] = {
    ProfessionType.SPACE_ENGINEERS: 30,
    ProfessionType.STRUCTURAL_ENGINEERS: 25,
    ProfessionType.MINING_ENGINEERS: 20,
    ProfessionType.RESEARCH_SCIENTISTS: 40,
    ProfessionType.AGRICULTURAL_SCIENTISTS: 30,
    ProfessionType.MEDICAL_PROFESSIONALS: 35,
    ProfessionType.TERRAFORM_ENGINEERS: 35,
    ProfessionType.COMBAT_PILOTS: 25,
    ProfessionType.DEFENSE_COORDINATORS: 30,
    ProfessionType.STRATEGIC_ANALYSTS: 35,
    ProfessionType.TRADE_SPECIALISTS: 20,
    ProfessionType.INDUSTRIAL_MANAGERS: 25,
}


class ColonistProfession(Base):
    """Specialist headcount for one profession on one planet."""

    __tablename__ = "colonist_professions"
    __table_args__ = (
        UniqueConstraint("planet_id", "profession", name="uq_colonist_profession_planet_profession"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    planet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("planets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profession = Column(String(40), nullable=False)
    count = Column(Integer, nullable=False, default=0)
