"""
Docking slip occupancy and wait-queue models.

Canon reference: FEATURES/economy/docking-slips (sw2102-docs). Stations expose
a finite pool of TRANSIENT docking slips sized by station kind; a docked ship
occupies exactly one. v1 enforces only the transient pool — `slip_class` is
carried on the occupancy row so long-term slip pools (which exist in canon)
can be added later without a schema change.

BACKFILL NOTE: players who docked before this feature shipped have no
occupancy row. The occupancy table is the single source of truth for slot
consumption — legacy docked players simply do not hold slips (acceptable),
and release/undock must tolerate a missing row silently.

These are new tables; `Base.metadata.create_all` (run at startup) covers all
environments — no Alembic migration is needed.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class DockingSlipOccupancy(Base):
    """One row per ship currently holding a transient docking slip."""
    __tablename__ = "docking_slip_occupancies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A player occupies at most one slip anywhere in the galaxy.
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    ship_id = Column(UUID(as_uuid=True), nullable=True)
    # 'transient' is the only class enforced in v1; carried for canon's
    # long-term slip pools.
    slip_class = Column(String(20), nullable=False, default="transient", server_default="transient")
    docked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fee_paid = Column(Integer, nullable=False, default=0, server_default="0")

    def __repr__(self) -> str:
        return (
            f"<DockingSlipOccupancy station={self.station_id} player={self.player_id} "
            f"class={self.slip_class} docked_at={self.docked_at}>"
        )


class DockingQueueEntry(Base):
    """FIFO wait-queue entry for a station whose transient slips are full."""
    __tablename__ = "docking_queue_entries"

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
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("station_id", "player_id", name="uq_docking_queue_station_player"),
    )

    def __repr__(self) -> str:
        return f"<DockingQueueEntry station={self.station_id} player={self.player_id}>"
