"""Append-only ledger of every Team treasury-affecting event.

Mirrors ``RegionalTreasuryEntry`` (region.py — ADR-0059 N-I4): captures the
before/after balance of the affected ``treasury_<resource>`` column plus the
signed delta, so a team's running treasury is auditable and the history tab can
render newest-first who-moved-what. One row is written per mutation, in the SAME
transaction as the balance change (single-writer), by ``TeamService`` at each of
its three treasury mutation sites (deposit / withdraw / transfer-to-player).

Additive: this table is new; no existing behavior keys off it.
"""

import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class TreasuryTransaction(Base):
    """One immutable ledger row per team-treasury mutation."""

    __tablename__ = "team_treasury_transactions"

    # Canon-silent taxonomy of what moved the balance. The set is open by design
    # (a plain String, like RegionalTreasuryEntry.cause_type) so future treasury
    # mechanics — tax, payout — can append new kinds without a migration.
    # [NO-CANON] the kind strings themselves are not specified in sw2102-docs;
    # these mirror the existing TeamService mutation sites and are the proposed
    # values (flagged for DECISIONS.md).
    KIND_DEPOSIT = "deposit"            # member → treasury
    KIND_WITHDRAW = "withdraw"          # treasury → the acting member
    KIND_TRANSFER = "transfer"          # treasury → another named member
    KIND_COMBAT_LOOT = "combat_loot"    # battle loot won/lost (fleet_service resolve)
    KIND_TAX = "tax"                    # future: skim on a mechanic (no writer yet)
    KIND_PAYOUT = "payout"             # future: scheduled disbursement (no writer yet)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Which treasury column this row moved (e.g. "credits", "quantum_crystals").
    # Stored alongside the amount so the history can be filtered/rendered per
    # resource without parsing the reason string.
    resource_type = Column(String(50), nullable=False)

    # The kind of movement (see the KIND_* constants above).
    kind = Column(String(30), nullable=False)

    # Signed-by-convention magnitude moved this event (always > 0; the kind tells
    # direction). Mirrors the other transaction models which store a positive
    # amount + a type, rather than a signed delta on the amount itself.
    amount = Column(Integer, nullable=False)

    # The treasury_<resource> balance AFTER this event landed — lets the history
    # render a running balance without re-summing the whole ledger.
    balance_after = Column(Integer, nullable=False)

    # Who initiated the movement. Nullable + SET NULL so the ledger survives a
    # player deletion (the row is the audit record; the actor may be gone).
    actor_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Human-readable note (e.g. "Aria deposited 500 credits", or a transfer
    # recipient). Free-form; not parsed by code.
    reason = Column(String(500), nullable=True)

    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    team = relationship("Team")
    actor = relationship("Player", foreign_keys=[actor_player_id])

    __table_args__ = (
        # Newest-first history for one team is the hot path.
        Index("ix_team_treasury_tx_team_created", "team_id", "created_at"),
    )

    def __repr__(self):
        return (
            f"<TreasuryTransaction(team_id='{self.team_id}', kind='{self.kind}', "
            f"resource='{self.resource_type}', amount={self.amount})>"
        )
