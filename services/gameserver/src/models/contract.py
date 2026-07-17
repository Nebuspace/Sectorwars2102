"""
Trade Contract model -- WO-ECON-CONTRACT-1-KERNEL, steps 1-3 of contracts.md's
own build order (model+migration -> NPC cargo_delivery generator -> API
routes). Player-issued posting + escrow, insurance, and disputes are later
steps (4, 6, 7) and are NOT exercised by this WO -- their columns exist now
(the schema below is the FULL contracts.md:25-63 table, built as one
additive whole per dispatch) but stay inert until a later WO wires them.

Canon: FEATURES/economy/contracts.md. Two documented state-machine sections
exist in that doc and they DISAGREE on terminal-state naming: the schema
table (:34) and the state-transition diagram (:65-86) both use `expired`
for a deadline lapsing on a POSTED (never-accepted) contract; a separate,
older "Contract lifecycle" table (:175-195) instead uses `failed` for the
same class of event. Per explicit dispatch instruction this model follows
the SCHEMA + state-transition-diagram naming (`expired`) -- the older table
is doc-cleanup debt for the orchestrator to reconcile, not something this
WO silently resolves by picking a third name.

[NO-CANON] `commodity_type` is String(50), NOT a hardcoded Postgres enum.
contracts.md:37 says "One of the canonical seven" but that phrase predates
src/models/resource.py's live registry (already a documented doc-gap --
see resource.py's own ResourceType docstring on the naming mismatch); a
hardcoded enum here would immediately drift from the registry the moment a
new commodity ships. Validity is a SERVICE-layer check against the live
registry (contract_service.py), not a column-level CHECK constraint.
"""
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
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class ContractIssuerType(enum.Enum):
    NPC = "npc"
    PLAYER = "player"


class ContractType(enum.Enum):
    CARGO_DELIVERY = "cargo_delivery"
    BULK_PROCUREMENT = "bulk_procurement"
    EXPRESS_DELIVERY = "express_delivery"
    HAZARDOUS_TRANSPORT = "hazardous_transport"
    REFUGEE_TRANSPORT = "refugee_transport"
    ACQUISITION_BOUNTY = "acquisition_bounty"
    ESCORT = "escort"


class ContractStatus(enum.Enum):
    POSTED = "posted"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    PARTIAL_FULFILLED = "partial_fulfilled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISPUTED = "disputed"
    EXPIRED = "expired"


class ContractEscrowState(enum.Enum):
    HELD = "held"
    RELEASED = "released"
    DISPUTED = "disputed"
    REFUNDING = "refunding"


class ContractDisputeResolution(enum.Enum):
    FULL_PAYOUT = "full_payout"
    PARTIAL_PAYOUT = "partial_payout"
    REFUND = "refund"
    SPLIT = "split"
    PENALTY = "penalty"


class ContractInsuranceCoverageTier(enum.Enum):
    BASIC = "basic"
    STANDARD = "standard"
    HAZARD = "hazard"


class Contract(Base):
    """The central ledger for every trade contract -- NPC-posted cargo runs,
    player acquisition bounties, escort jobs (contracts.md:25). Full schema
    built as one additive whole; only the `posted -> accepted -> completed`
    / `abandon` / `expire` transitions on `cargo_delivery` are exercised by
    this WO (contract_service.py) -- bulk-procurement partial fulfillment,
    player-issued posting/escrow, insurance, and disputes are later build
    steps and read/write none of their columns yet."""

    __tablename__ = "contracts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # WO-SWEEP-CONTRACT-ENUM: values_callable is REQUIRED on all six enum
    # columns below (same defect class as WO-SWEEP-WARPLAYER-ENUM). Plain
    # SQLAlchemy Enum(PyEnum) serializes the member NAME (e.g. "NPC",
    # "POSTED") by default -- but migration 1aab831e9008 created the six
    # Postgres enum TYPES from the lowercase VALUES ('npc'/'player',
    # 'posted'/'accepted'/..., etc. -- see ISSUER_TYPE_VALUES/STATUS_VALUES/
    # etc. in that migration). Without values_callable, every INSERT/UPDATE
    # and every enum-compared read against a real (values-built) Postgres DB
    # fails with "invalid input value for enum". Python-side member names
    # (ContractStatus.POSTED etc.) are completely unchanged; every existing
    # call site keeps working.
    issuer_type = Column(
        Enum(
            ContractIssuerType, name="contract_issuer_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    # [NO-CANON] contracts.md:31 types this "UUID / Integer -- FK -> Player.id
    # (player) or NPC identifier (npc)" without defining what an "NPC
    # identifier" is -- there is no NPC-registry model in this codebase.
    # For issuer_type=npc this WO sets issuer_id = destination_station_id,
    # NOT origin_station_id -- the schema's OWN board-listing index
    # (contracts.md:63, "(status, destination_station_id, posted_at DESC)
    # for board listings") keys board queries on the destination, and
    # :96's "Bounty-style acquisition contracts where THIS STATION IS THE
    # DESTINATION" reads naturally as: the destination station is the one
    # posting/wanting the job on ITS OWN board ("NPC contracts spawned by
    # the generator AT this station", :104) -- origin_station_id is just
    # the suggested/reserved pickup point (contracts.md:115), which for a
    # cargo_delivery is a DIFFERENT station's stock, not the issuer's own.
    # Not a real FK (an NPC-issued row's issuer_id points at `stations`, a
    # player-issued row's at `players`; a single FK can't target both, so
    # no ForeignKey() is declared on this column).
    issuer_id = Column(UUID(as_uuid=True), nullable=False)
    acceptor_player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)

    contract_type = Column(
        Enum(
            ContractType, name="contract_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    status = Column(
        Enum(
            ContractStatus, name="contract_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=ContractStatus.POSTED,
        server_default=ContractStatus.POSTED.value,
    )

    origin_station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    destination_station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)

    commodity_type = Column(String(50), nullable=True)  # null for escort
    quantity = Column(Integer, nullable=True)  # null for escort

    payment = Column(Numeric(19, 2), nullable=False)
    penalty = Column(Numeric(19, 2), nullable=False)
    acceptance_fee_pct = Column(Numeric(5, 2), nullable=False, default=2.0, server_default=text("2.0"))

    escrow_amount = Column(Numeric(19, 2), nullable=False, default=0, server_default=text("0"))
    escrow_state = Column(
        Enum(
            ContractEscrowState, name="contract_escrow_state",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=ContractEscrowState.HELD,
        server_default=ContractEscrowState.HELD.value,
    )

    faction_id = Column(UUID(as_uuid=True), ForeignKey("factions.id", ondelete="SET NULL"), nullable=True)
    reputation_reward = Column(Integer, nullable=True)
    reputation_penalty = Column(Integer, nullable=True)

    deadline = Column(DateTime(timezone=True), nullable=False)
    posted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    partial_fulfilled_amount = Column(Integer, nullable=True)
    partial_fulfilled_payout = Column(Numeric(19, 2), nullable=False, default=0, server_default=text("0"))

    dispute_filed_at = Column(DateTime(timezone=True), nullable=True)
    dispute_resolution = Column(
        Enum(
            ContractDisputeResolution, name="contract_dispute_resolution",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
    )
    dispute_resolved_at = Column(DateTime(timezone=True), nullable=True)
    dispute_notes = Column(Text, nullable=True)
    escalated_to_admin = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    insurance_coverage_tier = Column(
        Enum(
            ContractInsuranceCoverageTier, name="contract_insurance_coverage_tier",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
    )
    insurance_premium_paid = Column(Numeric(19, 2), nullable=False, default=0, server_default=text("0"))
    insurance_claim_filed = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # WO-CONTRACT-1b-CLAIM-SAFETY: the real, drawable claims fund a covered
    # failure-event offset is settled from. Previously only a `post_
    # player_contract()` PARAMETER folded into `escrow_amount` at posting
    # time and never stored on its own (contract_service.py:738/:804) --
    # the split was lost the instant escrow_amount was computed, leaving
    # nothing for a claim to actually draw against. This column is the
    # REMAINING pool balance: funded at post time (player-issued only;
    # NPC-issued rows stay 0, no generator sets it), drawn down by
    # `sweep_expired_accepted_contracts`'s claim-offset math as each
    # contract's own failure event consumes it, floored at 0. Existing
    # rows backfill to 0 -- the pool amount any of them originally carried
    # (if any) is unrecoverable from `escrow_amount` alone, and 0 is the
    # conservative floor (no free coverage manufactured for a pre-existing
    # row). See contract_insurance.py's claim-offset engine.
    insurance_pool_reserve = Column(Numeric(19, 2), nullable=False, default=0, server_default=text("0"))

    # Player-issued contracts (contracts.md:61); NPC-issued rows default to
    # [destination_station_id] at generation time (still visible on their own
    # destination's board without a separate multi-station posting flow).
    posting_stations = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)

    acceptor = relationship("Player", foreign_keys=[acceptor_player_id])
    origin_station = relationship("Station", foreign_keys=[origin_station_id])
    destination_station = relationship("Station", foreign_keys=[destination_station_id])
    faction = relationship("Faction", foreign_keys=[faction_id])

    __table_args__ = (
        Index("idx_contract_board_listing", "status", "destination_station_id", "posted_at"),
        Index("idx_contract_issuer_status", "issuer_id", "status"),
        Index("idx_contract_acceptor_status", "acceptor_player_id", "status"),
        Index("idx_contract_deadline", "deadline"),
        Index("idx_contract_dispute_queue", "status", "dispute_filed_at"),
    )

    def __repr__(self) -> str:
        ctype = self.contract_type.value if self.contract_type else "?"
        cstatus = self.status.value if self.status else "?"
        return f"<Contract {self.id} {ctype} {cstatus}>"
