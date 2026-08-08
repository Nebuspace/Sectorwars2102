"""Add ship salvage-break fields (WO-BUILD-SALVAGE-BREAK-FIELDS).

Adds the two salvage-break columns DATA_MODELS/ships.md documents as the
sole remaining schema gap: salvage_break_in_progress_by_id (non-NULL when a
salvage break is underway on a Drifting ship, by the named player) and
salvage_break_started_at (start timestamp; duration scales by ship class --
Scout 1h / Cargo Hauler 4h / Carrier 12h). Schema-only -- no consuming
service exists yet.

Fully additive — two new nullable columns only, no existing column changes.

Revision ID: a3f7c91e05b8
Revises: f1c8a3d92b4e
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a3f7c91e05b8"
down_revision = "f1c8a3d92b4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ships",
        sa.Column(
            "salvage_break_in_progress_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "ships",
        sa.Column(
            "salvage_break_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ships", "salvage_break_started_at")
    op.drop_column("ships", "salvage_break_in_progress_by_id")
