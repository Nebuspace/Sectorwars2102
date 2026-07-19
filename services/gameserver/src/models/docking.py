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

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class DockingSlipOccupancy(Base):
    """One row per ship currently holding a transient docking slip.

    WO-P9-realtime-npc-trader-slips: TRADER-archetype NPCs occupy real
    slips too (npc-traders.md § Market participation — "traders occupy
    real docking slips like players"), via the SAME occupancy table the
    player dock path uses rather than a parallel store, so the existing
    `occupied = len(occupancies)` count (docking_service.acquire,
    routes/trading.py's GET .../slips) automatically includes them with
    no reader-side change. Mirrors the player_id/npc_id dual-nullable-FK
    pattern already established on MarketTransaction
    (models/market_transaction.py) for the same NPC-attribution need.
    """
    __tablename__ = "docking_slip_occupancies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Exactly one of player_id / npc_id is set per row (enforced by the
    # ck_docking_slip_occupancy_exactly_one_owner CHECK constraint below).
    # A player -- or an NPC trader -- occupies at most one slip anywhere
    # in the galaxy (each FK carries its own UNIQUE constraint; Postgres
    # UNIQUE permits multiple NULLs, so an NPC-owned row's NULL player_id
    # never collides with another NPC-owned row's NULL player_id, and
    # vice versa).
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )
    npc_id = Column(
        UUID(as_uuid=True),
        ForeignKey("npc_characters.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )
    ship_id = Column(UUID(as_uuid=True), nullable=True)
    # Slip class vocabulary (canon: FEATURES/economy/docking-slips):
    #   'transient'       — routine docking; 5 min–24 h; FIFO availability
    #   'long_term'       — multi-day mooring (1–30 days); optional pre-book;
    #                       200 cr/day rental; for out-of-game players or
    #                       stored ships between trade routes. Added in v2.
    # Construction slip classes ('construction', 'specialized_construction')
    # live on ConstructionReservation, not DockingSlipOccupancy.
    SLIP_CLASS_TRANSIENT = "transient"
    SLIP_CLASS_LONG_TERM = "long_term"

    slip_class = Column(String(20), nullable=False, default="transient", server_default="transient")
    docked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fee_paid = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "(player_id IS NOT NULL) != (npc_id IS NOT NULL)",
            name="ck_docking_slip_occupancy_exactly_one_owner",
        ),
    )

    def __repr__(self) -> str:
        owner = f"player={self.player_id}" if self.player_id else f"npc={self.npc_id}"
        return (
            f"<DockingSlipOccupancy station={self.station_id} {owner} "
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
