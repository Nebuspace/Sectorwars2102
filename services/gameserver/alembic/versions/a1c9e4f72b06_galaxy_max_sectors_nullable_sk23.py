"""Make Galaxy.max_sectors nullable soft observability target (SK23).

ADR-0050 SK23: Galaxy has no hard sector cap — max_sectors is a soft
operator-dashboard target, not a provisioning pre-check. Column retained;
nullable so operators can leave it unset. Existing rows keep their value.

Revision ID: a1c9e4f72b06
Revises: c2f8a1e94b70
Create Date: 2026-08-05 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a1c9e4f72b06"
down_revision = "c2f8a1e94b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "galaxies",
        "max_sectors",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill NULLs before restoring NOT NULL so downgrade does not fail.
    op.execute("UPDATE galaxies SET max_sectors = 500 WHERE max_sectors IS NULL")
    op.alter_column(
        "galaxies",
        "max_sectors",
        existing_type=sa.Integer(),
        nullable=False,
    )
