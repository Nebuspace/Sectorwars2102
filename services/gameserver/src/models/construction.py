"""
Ship construction reservation model (TradeDock shipyard).

Canon reference: FEATURES/economy/tradedock-shipyard + ADR-0039 (sw2102-docs).
One row tracks a player's ship build through the full pipeline:

    requested -> queued -> hold_active -> deposit_collected -> frame_assembly
    -> systems_integration -> outfitting -> final_assembly -> complete
    -> claimed | cancelled | forfeited

`requested` exists only transiently inside create_reservation (the deposit is
charged in the same transaction that enters the queue); it is part of the
canon machine and kept for fidelity. All timing columns hold ABSOLUTE
wall-clock deadlines computed through src.core.game_time (scaled_deadline),
so GAME_TIME_SCALE compresses every window uniformly on dev. A phase state
with `phase_deadline` NULL means the phase clock is PAUSED — its milestone is
unpaid and/or its resource checkpoint is unmet (the status payload reports
exactly what is needed).

This is a new table; `Base.metadata.create_all` (run at startup) covers all
environments — no Alembic migration is needed.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.core.database import Base


class ConstructionReservation(Base):
    """One ship-construction project at a TradeDock station."""
    __tablename__ = "construction_reservations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ShipType enum NAME (validated against ShipType + the canon build table
    # by the service); stored as String so the table needs no enum migration.
    ship_type = Column(String(30), nullable=False)
    state = Column(String(30), nullable=False, index=True, default="requested")
    ship_name = Column(String(100), nullable=True)

    # Canon all-in project cost; every payment fraction derives from it.
    total_cost = Column(Integer, nullable=False)
    deposit_paid = Column(Integer, nullable=False, default=0)
    # Cumulative CASH the player has paid toward the project (incl. deposit,
    # excl. rent, excl. applied queue_bonus_credit) — the cancel-refund base.
    credits_paid = Column(Integer, nullable=False, default=0)
    # Paid flags per milestone: {"deposit": bool, "keel_laid": bool,
    # "hull_complete": bool, "final": bool}
    milestones = Column(JSONB, nullable=False, default=dict)
    # Resource bundle (ore/equipment/organics) required for the build and the
    # cumulative ATOMIC, IRREVERSIBLE deliveries against it (ADR-0039).
    resources_required = Column(JSONB, nullable=False, default=dict)
    resources_delivered = Column(JSONB, nullable=False, default=dict)

    # Warp Jumper consumes a Tier-A SPECIALIZED slip; everything else uses
    # the standard construction pool.
    uses_specialized_slip = Column(Boolean, nullable=False, default=False)

    # Current phase's completion time when the phase clock is running;
    # NULL while the phase is paused (milestone/resource gate unmet).
    phase_deadline = Column(DateTime(timezone=True), nullable=True)
    # 24 canonical-hour slip hold window (queued -> hold_active).
    hold_expires_at = Column(DateTime(timezone=True), nullable=True)
    # 7 canonical-day claim window once the build completes.
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Slip rent is accrued lazily: rent is owed for time past rent_paid_until;
    # 3 consecutive canonical days unpaid forfeits the build.
    rent_paid_until = Column(DateTime(timezone=True), nullable=True)
    rent_owed_since = Column(DateTime(timezone=True), nullable=True)

    # Credit redistributed from a forfeited hold's deposit (50% to the
    # next-in-queue reservation); applied against future milestone payments.
    queue_bonus_credit = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ConstructionReservation {self.ship_type} at station={self.station_id} "
            f"player={self.player_id} state={self.state}>"
        )
