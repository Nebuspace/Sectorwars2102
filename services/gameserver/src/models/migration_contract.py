"""MigrationContract — pioneer migration contracts brokered at a capital
population hub's Pioneer Office (FEATURES/planets/colonization.md).

Canon places the Pioneer Office at the Capital Sector's Class-0 station;
this contract layer instead surfaces it on the population-hub PLANET
(landed) as a deliberate UX choice — the station colonist-commodity buy
flow is left intact and untouched.

A contract is a tracked cohort: the player brokers N pioneers at a locked
per-pioneer fee, ferries them from the hub into ship cargo in
cargo-limited batches (``loaded``), and settles them on frontier worlds
over multiple trips (``delivered``) via the existing claim / disembark
flow. The contract ledger watches settlement and advances ``delivered``;
it never moves colonists itself.

States:
  BROKERED      — created, fee locked, nothing loaded yet
  IN_PROGRESS   — at least one pioneer loaded into cargo and/or settled
  FULFILLED     — delivered == cohort_total
  VOID          — cancelled by the player (only while loaded == 0), or
                  reabsorbed when the carrying ship was destroyed

Invariant: ``delivered + loaded <= cohort_total`` and
``delivered <= cohort_total``. Cargo is fungible (a single
``cargo.contents.colonists`` integer, no per-pod tagging); ``loaded`` is
the contract's accounting mirror, and settlement is attributed across a
player's contracts FIFO by ``created_at``.
"""

import uuid
import enum

from sqlalchemy import (
    Column, DateTime, Integer, ForeignKey, Enum, Index, func,
)
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class MigrationContractStatus(enum.Enum):
    BROKERED = "BROKERED"
    IN_PROGRESS = "IN_PROGRESS"
    FULFILLED = "FULFILLED"
    VOID = "VOID"


class MigrationContract(Base):
    __tablename__ = "migration_contracts"
    __table_args__ = (
        # The "list my active contracts" query and the fulfillment match.
        Index("ix_migration_contracts_player_status", "player_id", "status"),
        # The load-batch lookup (contract at the hub you're landed on).
        Index("ix_migration_contracts_player_source",
              "player_id", "source_planet_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The hub planet the contract was brokered at — loading is gated to it.
    source_planet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("planets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized global sector id (e.g. 1001) for "return to {sector}"
    # messaging without a join.
    source_sector_id = Column(Integer, nullable=False)
    # Pioneers brokered in this cohort.
    cohort_total = Column(Integer, nullable=False)
    # Pioneers currently riding in cargo against THIS contract (not settled).
    loaded = Column(Integer, nullable=False, default=0)
    # Pioneers settled at frontier worlds.
    delivered = Column(Integer, nullable=False, default=0)
    # The clamped 30–80 cr per-pioneer fee snapshotted at broker time.
    fee_per_pioneer_locked = Column(Integer, nullable=False)
    status = Column(
        Enum(MigrationContractStatus, name="migration_contract_status"),
        nullable=False,
        default=MigrationContractStatus.BROKERED,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    @property
    def remaining_to_load(self) -> int:
        return max(0, (self.cohort_total or 0) - (self.delivered or 0) - (self.loaded or 0))

    def __repr__(self) -> str:
        return (
            f"<MigrationContract player={self.player_id} "
            f"{self.delivered}/{self.cohort_total} delivered, "
            f"{self.loaded} loaded ({self.status.name})>"
        )
