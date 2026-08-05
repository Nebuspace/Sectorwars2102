"""MiningHarvest — persisted in-flight asteroid harvest (WO-MINING-ASYNC-HARVEST).

Canon (``FEATURES/economy/mining.md`` § PvP interaction) requires an
interruptible window while ``Ship.status = MINING``. The prior kernel
set and cleared MINING inside one request, so no peer could interrupt.
This table holds the durable in-progress row: turns are prepaid at
start, yields apply only on COMPLETED resolve, and INTERRUPTED cancels
with the 50% turn-refund path (WO-MINING-PVP-INTERRUPT).

Greenfield additive — new table only; no existing columns altered.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class MiningHarvestStatus(enum.Enum):
    """Lifecycle of one asteroid harvest attempt."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class MiningHarvest(Base):
    """One asteroid-harvest attempt, interruptible while PENDING."""

    __tablename__ = "mining_harvests"
    __table_args__ = (
        # Scheduler sweep: due PENDING rows by resolves_at.
        Index(
            "ix_mining_harvests_status_resolves",
            "status",
            "resolves_at",
        ),
        # Player history / cockpit poll.
        Index("ix_mining_harvests_player", "player_id"),
        # Partial unique: at most one in-flight harvest per ship
        # (created in the Alembic migration — SQLAlchemy cannot express
        # WHERE status='PENDING' on UniqueConstraint portably).
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    ship_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ships.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Global human-readable sector id (matches Ship.sector_id /
    # Sector.sector_id). Snapshotted at start so resolve/interrupt stay
    # coherent if the hull is moved by an external force mid-flight.
    sector_id = Column(Integer, nullable=False)
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(
        Enum(MiningHarvestStatus, name="mining_harvest_status"),
        nullable=False,
        default=MiningHarvestStatus.PENDING,
    )
    # Prepaid turn cost (canon HARVEST_TURN_COST=5). Interrupt refunds
    # floor(turns_spent/2) per mining.md § Interrupt behaviour.
    turns_spent = Column(Integer, nullable=False)
    # Snapshots for resolve-time yield / AM-rep (avoid re-reading mutable
    # equipment or license state after the attempt began).
    laser_level = Column(Integer, nullable=False)
    richness_tier = Column(Integer, nullable=False)
    am_claimed = Column(Boolean, nullable=False, default=False)
    has_license = Column(Boolean, nullable=False, default=False)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # When the scheduler may complete this PENDING row. Wall-clock
    # authoritative (same shape as harmonization_completes_at).
    resolves_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    # Filled on COMPLETED; null while PENDING / on cancel-interrupt.
    ore_yield = Column(Integer, nullable=True)
    precious_metals_yield = Column(Integer, nullable=True)
    quantum_shards_yield = Column(Integer, nullable=True)
    am_rep_delta = Column(Integer, nullable=True)
    # Stable reason code when CANCELLED / INTERRUPTED (e.g. pvp_attack).
    terminal_reason = Column(String(64), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MiningHarvest {self.id} ship={self.ship_id} "
            f"status={self.status} sector={self.sector_id}>"
        )
