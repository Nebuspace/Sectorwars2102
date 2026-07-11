"""StorageLocker + ContractCargoDeposit models -- WO-STORE-LOCKER-MODEL,
S1 foundation of the storage kernel (audit/design-briefs/heist-brief.html
"01 / THE KERNEL"). Schema only, additive, NO business logic -- the
remaining 4 S1 WOs (DEPOSIT-FLOW / FEE-ACCRUAL / EXPIRY-CLAIMABLE /
BAY-UI) build directly on top of this.

Canon (heist-brief.html): rent a locker at the contract's DESTINATION
SpaceDock (the existing Station model), deliver the commodity in
multi-trip installments, a flat 1cr/unit/day rent accrues (D16,
provisional), the contract completes once the locker holds the full
quantity. Miss the deadline -> cargo converts to CLAIMABLE storage (the
player keeps it, rent keeps ticking, retrieve later). No accept-time
capacity guard needed -- this is what fixes the original bug (a
too-small ship can't complete a large contract in one trip).

S2 forward-compat (heist-brief.html "02 / THE SPINE" risk ladder + "04 /
THE ECONOMY" tier table): `tier` and `risk_state` columns exist NOW, both
fully enumerated (BASIC/REINFORCED/VAULT; SECURE/WATCHED/TARGETED/
BREACHED) even though S1 only ever writes BASIC/SECURE -- so when S2's
heist mechanics land, no destructive migration is needed; at most an
additive enum member if the eventual tuning adds a state this brief
didn't anticipate (never a column change).

[NO-CANON, flag for review] money-field type: the assigning WO named
`Player.credits` (Integer) as the type to match. This model instead
follows `Contract`'s own convention -- `payment`/`penalty`/
`escrow_amount`/`partial_fulfilled_payout`/`insurance_premium_paid` are
ALL `Numeric(19, 2)`, zero Integer money columns anywhere on that table
(confirmed via alembic/versions/1aab831e9008_add_contracts_table.py).
`rent_rate`/`accrued_fee` here are contract-adjacent economics in the
exact same category, and canon's own WO-STORE-FEE-ACCRUAL description
explicitly calls for "ROUND_HALF_UP" rounding (decimal-precision
language) plus S2's tier multipliers are non-integer (~2.5x / ~5x) --
Numeric(19, 2) is the right precedent to match, not Player.credits'
whole-integer wallet-balance convention. Flagged for review, not
silently decided.
"""
import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class StorageLockerStatus(enum.Enum):
    ACTIVE = "active"        # rent accruing, tied to contract_id (or standalone-rented)
    CLAIMABLE = "claimable"  # deadline missed -- owner keeps cargo, rent keeps ticking
    RELEASED = "released"    # emptied + vacated (retrieved or contract completed)


class StorageLockerTier(enum.Enum):
    """S1 writes BASIC only; REINFORCED/VAULT are S2 forward-compat
    (heist-brief.html "04 / THE ECONOMY" tier table -- rent 1x/~2.5x/~5x,
    break-in difficulty low/medium/high)."""
    BASIC = "basic"
    REINFORCED = "reinforced"
    VAULT = "vault"


class StorageLockerRiskState(enum.Enum):
    """S1 writes SECURE only; the remaining three are S2 forward-compat
    (heist-brief.html "02 / THE SPINE" risk ladder, driven by dwell time
    + station security once the heist WOs land)."""
    SECURE = "secure"
    WATCHED = "watched"
    TARGETED = "targeted"
    BREACHED = "breached"


class StorageLocker(Base):
    """A rented cargo locker at a Station (canon prose calls it a
    "SpaceDock" -- Station is the existing model that concept maps onto,
    the same target Contract.origin_station_id / destination_station_id
    already use). Rent accrues at `rent_rate` cr/unit/day against the
    deposited quantity; a later WO (FEE-ACCRUAL) settles `accrued_fee`
    incrementally from `last_fee_settled_at` forward -- this model only
    carries the ledger columns, it does not compute or charge anything."""
    __tablename__ = "storage_lockers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    owner_player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    # "SpaceDock" in canon prose -> the existing Station model (table
    # `stations`) -- Contract.origin_station_id / destination_station_id
    # are the established precedent for this exact FK shape and ondelete.
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    # Nullable: null once the locker is standalone CLAIMABLE storage no
    # longer tied to the contract that originally spawned it (canon: "you
    # keep it, rent keeps ticking, retrieve later").
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True)

    status = Column(
        Enum(
            StorageLockerStatus, name="storage_locker_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False, default=StorageLockerStatus.ACTIVE,
        server_default=StorageLockerStatus.ACTIVE.value,
    )
    tier = Column(
        Enum(
            StorageLockerTier, name="storage_locker_tier",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False, default=StorageLockerTier.BASIC,
        server_default=StorageLockerTier.BASIC.value,
    )
    risk_state = Column(
        Enum(
            StorageLockerRiskState, name="storage_locker_risk_state",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False, default=StorageLockerRiskState.SECURE,
        server_default=StorageLockerRiskState.SECURE.value,
    )

    # D16 (provisional): flat 1cr/unit/day, stored PER-LOCKER (not read
    # from a global constant at accrual time) so a future tier-multiplier
    # change can never retroactively reprice an already-rented locker.
    rent_rate = Column(Numeric(19, 2), nullable=False, default=1, server_default="1")
    # Incrementally settled by a later WO (FEE-ACCRUAL) from
    # last_fee_settled_at forward. This is the running "owed" ledger only
    # -- nothing here deducts the owner's credits.
    accrued_fee = Column(Numeric(19, 2), nullable=False, default=0, server_default="0")
    last_fee_settled_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Rent-start anchor; doubles as the S2 dwell-time anchor (heist-
    # brief.html "02 / THE SPINE": "dwell time... the longer it sits").
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner = relationship("Player")
    station = relationship("Station")
    contract = relationship("Contract")
    deposits = relationship("ContractCargoDeposit", back_populates="locker", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<StorageLocker {self.id} station={self.station_id} status={self.status.name}>"


class ContractCargoDeposit(Base):
    """One row per delivery INSTALLMENT into a locker -- an audit trail,
    not a mutable running total. A later WO (DEPOSIT-FLOW) sums `quantity`
    across a locker's rows (optionally filtered by `commodity`) to compare
    the accumulated total against the contract's required quantity.
    `commodity`/`quantity` column types match Contract.commodity_type /
    Contract.quantity exactly (String(50) / Integer)."""
    __tablename__ = "contract_cargo_deposits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    locker_id = Column(UUID(as_uuid=True), ForeignKey("storage_lockers.id", ondelete="CASCADE"), nullable=False)
    commodity = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    # SET NULL (not CASCADE): a deleted player account must never
    # silently erase this locker's provable deposit history / running
    # total -- the row (and the cargo it accounts for) outlives the
    # depositor reference.
    deposited_by = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    deposited_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    locker = relationship("StorageLocker", back_populates="deposits")
    depositor = relationship("Player")

    def __repr__(self):
        return f"<ContractCargoDeposit {self.id} locker={self.locker_id} {self.quantity}x {self.commodity}>"
