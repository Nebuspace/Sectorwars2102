"""Add Planet.termination_compensated_at (WO-ESCALATE-CYCLE26-DESIGN-FLAGS).

Idempotency marker for process_planet_termination so dispatch_terminated_cleanup
can leave Region.cleanup_completed_at NULL while station termination remains
discovery-only, without re-minting Genesis compensation on every daily re-entry.

Revision ID: a8c3e1f92b47
Revises: e2c7a4f91b3d
Create Date: 2026-08-04 20:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a8c3e1f92b47"
down_revision = "e2c7a4f91b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "planets",
        sa.Column("termination_compensated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("planets", "termination_compensated_at")
