"""Add Region.cleanup_completed_at (WO-FIX-REGION-LIFECYCLE-GATE-CASCADE-DISPATCH).

Idempotency marker for region_lifecycle_service.dispatch_terminated_cleanup:
without it, a TERMINATED region past scheduled_hard_delete_at would be
re-discovered as "eligible" on every daily sweep, and (once wired to the
gate-cascade dispatch point) would re-run the planet-safe-transport /
Genesis-compensation cascade against the same planets repeatedly. Nullable,
set once when a region's cleanup cascade has actually been dispatched.

Fully additive -- one new nullable column, no existing column changes.

Revision ID: d1e9f4a6b23c
Revises: 6a5d5597591a
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d1e9f4a6b23c"
down_revision = "6a5d5597591a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regions",
        sa.Column("cleanup_completed_at", sa.TIMESTAMP(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("regions", "cleanup_completed_at")
