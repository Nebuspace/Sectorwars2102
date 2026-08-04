import uuid
import enum
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class ExpeditionStatus(enum.Enum):
    """Ground-expedition outcome (ADR-0091 §3). PENDING covers the optional
    EXPEDITION_DELAY_MINUTES window before the roll resolves; the roll itself
    resolves to exactly one of SUCCESS / PARTIAL / FAILURE at launch — there
    is no pre-stored candidate site to re-read later."""
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"


class Expedition(Base):
    """A single at-launch ground-expedition roll (ADR-0091 §1/§3). Every
    expedition is a fresh RNG roll generated at launch — there is no
    CandidateSite, no site_seed, no per-planet pre-stored result. `result`
    is the ephemeral SiteIntel payload (shape_class/template_id/usable_slots/
    energy/resources/hazards/native_life) on SUCCESS/PARTIAL; NULL on
    FAILURE/PENDING. Only the SETTLED result persists as the colony's grid
    (baked onto Planet.structures by structures.py, the single writer) —
    this row is the historical/ephemeral record of the roll itself, not a
    site cache."""
    __tablename__ = "expeditions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    planet_id = Column(UUID(as_uuid=True), ForeignKey("planets.id", ondelete="CASCADE"), nullable=False)
    # The ship the expedition launched from (in orbit at launch time, ADR-0091
    # §3). Nullable — a comped onboarding demo expedition (M39, `demo=True`)
    # runs at zero stakes and need not tie to a real hull.
    ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    status = Column(Enum(ExpeditionStatus, name="expedition_status"), nullable=False, default=ExpeditionStatus.PENDING)
    launched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Ephemeral SiteIntel payload on SUCCESS/PARTIAL (shape_class, template_id,
    # usable_slots, energy, resources, hazards, native_life); NULL on
    # FAILURE (ADR-0091 M19: no stored result from a FAILURE) and while PENDING.
    result = Column(JSONB, nullable=True)
    # M39 — the 2-3 free zero-stakes demo expeditions that teach the
    # re-roll/compare mechanic during onboarding. Demo expeditions cost
    # nothing and can never be the settling expedition (ADR-0091 §3).
    demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    player = relationship("Player", foreign_keys=[player_id])
    planet = relationship("Planet", foreign_keys=[planet_id])
    ship = relationship("Ship", foreign_keys=[ship_id])

    def __repr__(self):
        return f"<Expedition {self.id} planet={self.planet_id} status={self.status.name}>"
