"""Add Ship contested-registration-transfer columns
(WO-BUILD-SHIP-REGISTRY-CONTESTED-TRANSFER-SALVAGE-CLAIM)

Purely additive: five new nullable columns on ``ships``, no existing column
or table touched. Canon: SYSTEMS/ship-registry.md "Legal ownership transfer"
(30% fee, 24h owner-dispute window) and its eligibility check ("Borrowed by
the claimant for at least 1 hour", which needs a pilot-since timestamp that
did not previously exist anywhere in this codebase).

Revision ID: c4a8e2f7b910
Revises: a3f7c9e1d5b6
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "c4a8e2f7b910"
down_revision = "a3f7c9e1d5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ships",
        sa.Column("current_pilot_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column(
            "pending_transfer_claimant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ships",
        sa.Column("pending_transfer_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column("pending_transfer_deadline", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column("pending_transfer_fee_paid", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column(
            "pending_transfer_port_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ships", "pending_transfer_port_id")
    op.drop_column("ships", "pending_transfer_fee_paid")
    op.drop_column("ships", "pending_transfer_deadline")
    op.drop_column("ships", "pending_transfer_requested_at")
    op.drop_column("ships", "pending_transfer_claimant_id")
    op.drop_column("ships", "current_pilot_since")
