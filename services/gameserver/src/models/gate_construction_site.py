"""
Warp-gate staged-materials construction site (ADR-0078 + FEATURES/galaxy/
warp-gates.md#material-staging).

A Warp Jumper's 200-unit hold cannot fit a phase's 1,500 (Phase 1) or 1,530
(Phase 3) unit material total in a single trip, so each phase's bulk ORE /
EQUIPMENT / LUMEN_CRYSTALS accumulate here across many partial deposits
before the phase can be committed. One row exists per (beacon, phase):

  Phase 1 site — opened by deploy_beacon alongside the WarpGateBeacon row,
                 empty; required_ore/required_equipment only (no Lumen).
  Phase 3 site — opened lazily once the Phase 1 site finishes curing
                 (warp_gate_service._lazy_advance_site_cure — canon: "before
                 the next phase opens"); required_ore/required_equipment/
                 required_lumen. Consumed at anchor_focus's commit, and
                 refilled to full on a subsequent HARMONIZING-gate cancel
                 (warp-gates.md Phase 3 failure modes: refund goes to "the
                 construction site / player", not the Warp Jumper's hold).

Lifecycle per site: STAGING (ferrying materials in) -> CURING (fully staged,
advance-construction spent its 5 turns, 24-canonical-hour cure running) ->
READY (cure elapsed, available to draw) -> CONSUMED (drawn into the phase's
commit — Phase 3 only) or CANCELLED (beacon/gate abandoned).
"""
import uuid
import enum

from sqlalchemy import Column, DateTime, Integer, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class GateConstructionSiteStatus(enum.Enum):
    STAGING = "STAGING"      # materials still being ferried in
    CURING = "CURING"        # fully staged, turns spent, 24h cure running
    READY = "READY"          # cure elapsed -- available to draw
    CONSUMED = "CONSUMED"    # drawn into the phase's commit (Phase 3 only)
    CANCELLED = "CANCELLED"  # beacon/gate abandoned


class GateConstructionSite(Base):
    __tablename__ = "gate_construction_sites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beacon_id = Column(UUID(as_uuid=True), ForeignKey("warp_gate_beacons.id", ondelete="CASCADE"), nullable=False)
    # Set once anchor_focus creates the WarpGate row that draws this (Phase 3)
    # site; NULL for the Phase-1 site and for an unconsumed Phase-3 site.
    gate_id = Column(UUID(as_uuid=True), ForeignKey("warp_gates.id", ondelete="SET NULL"), nullable=True)
    phase = Column(Integer, nullable=False)  # 1 or 3 (warp-gates.md phases)

    # Required-totals snapshot (PHASE1_* / PHASE3_* at the moment this site
    # opened) -- a build in progress isn't affected if the canon constants
    # ever change.
    required_ore = Column(Integer, nullable=False, default=0)
    required_equipment = Column(Integer, nullable=False, default=0)
    required_lumen = Column(Integer, nullable=False, default=0)

    staged_ore = Column(Integer, nullable=False, default=0)
    staged_equipment = Column(Integer, nullable=False, default=0)
    staged_lumen = Column(Integer, nullable=False, default=0)

    # Cumulative turns spent via advance-construction on this site (progress
    # display only -- the 5-turn charge itself is applied to Player.turns).
    turns_applied = Column(Integer, nullable=False, default=0)
    cure_completes_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(GateConstructionSiteStatus, name="gate_construction_site_status"),
        nullable=False,
        default=GateConstructionSiteStatus.STAGING,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    beacon = relationship("WarpGateBeacon")
    gate = relationship("WarpGate")

    def __repr__(self):
        return (
            f"<GateConstructionSite {self.id} beacon={self.beacon_id} "
            f"phase={self.phase} ({self.status.name})>"
        )
