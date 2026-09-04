"""LEG-4177 — nullable pirate_holdings.outlaw_base_id FK to outlaw_bases.

Additive lodging-anchor kernel. Canon DATA_MODELS/pirate-holdings.md wants a
NOT NULL 1:1; existing holding rows have no OutlawBase to attach, so this
revision is nullable with no backfill. Unique index (Postgres allows many
NULLs) is the 1:1 guard. ON DELETE SET NULL so deleting a base does not
cascade-delete the holding.

No OutlawBase→NPCBarracks conversion.

Revision ID: e9b4c2a71d60
Revises: c3e8f1a92b70
Create Date: 2026-09-03 16:37:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e9b4c2a71d60"
down_revision = "c3e8f1a92b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pirate_holdings",
        sa.Column(
            "outlaw_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outlaw_bases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_pirate_holdings_outlaw_base_id",
        "pirate_holdings",
        ["outlaw_base_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_pirate_holdings_outlaw_base_id", table_name="pirate_holdings")
    op.drop_column("pirate_holdings", "outlaw_base_id")
