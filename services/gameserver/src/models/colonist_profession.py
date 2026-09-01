"""Colonist profession aggregates per planet (FEATURES/planets/professions.md)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TrainingCostPer100:
    """Provisional per-100-trainee recipe (ADR-0093 item 35 / LEG-DEC-804)."""

    credits: int
    equipment: int = 0
    organics: int = 0

    def scale(self, trainee_count: int) -> TrainingCostPer100:
        return TrainingCostPer100(
            credits=self.credits * trainee_count // 100,
            equipment=self.equipment * trainee_count // 100,
            organics=self.organics * trainee_count // 100,
        )

    def as_dict(self) -> dict[str, int]:
        out = {"credits": self.credits}
        if self.equipment:
            out["equipment"] = self.equipment
        if self.organics:
            out["organics"] = self.organics
        return out


# Per-100 anchor: Space Engineers canonical; military/economic ~= base; scientific ~1.5×
# (professions.md § Per-100 anchor). Research Scientists' canon "tech" maps to equipment
# provisionally — no planet tech stockpile column exists.
_BASE_COST_PER_100 = TrainingCostPer100(50_000, equipment=1_000)

PROFESSION_TRAINING_COST_PER_100: dict[ProfessionType, TrainingCostPer100] = {
    ProfessionType.SPACE_ENGINEERS: TrainingCostPer100(50_000, equipment=1_000),
    ProfessionType.STRUCTURAL_ENGINEERS: _BASE_COST_PER_100,
    ProfessionType.MINING_ENGINEERS: _BASE_COST_PER_100,
    ProfessionType.RESEARCH_SCIENTISTS: TrainingCostPer100(75_000, equipment=1_500),
    ProfessionType.AGRICULTURAL_SCIENTISTS: TrainingCostPer100(75_000, organics=1_500),
    ProfessionType.MEDICAL_PROFESSIONALS: TrainingCostPer100(75_000, equipment=1_500),
    ProfessionType.TERRAFORM_ENGINEERS: TrainingCostPer100(
        75_000, equipment=1_000, organics=500
    ),
    ProfessionType.COMBAT_PILOTS: _BASE_COST_PER_100,
    ProfessionType.DEFENSE_COORDINATORS: _BASE_COST_PER_100,
    ProfessionType.STRATEGIC_ANALYSTS: TrainingCostPer100(50_000),
    ProfessionType.TRADE_SPECIALISTS: TrainingCostPer100(50_000),
    ProfessionType.INDUSTRIAL_MANAGERS: _BASE_COST_PER_100,
}

# Real-time training durations in days (professions.md table).
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
    # NULL = legacy implicit (all trained specialists active); explicit int = owner assignment.
    active_count = Column(Integer, nullable=True)
