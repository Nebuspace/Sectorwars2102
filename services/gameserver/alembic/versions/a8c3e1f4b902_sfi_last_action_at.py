"""Add sector_faction_influence.last_action_at (LEG-INI-05 / LEG-65)

Additive nullable timestamptz so daily influence decay can judge idle without
self-resetting ``updated_at`` when the decay job writes. Write path
(``adjust_sector_influence``) sets ``last_action_at``; decay never does.

Revision ID: a8c3e1f4b902
Revises: a9c2e4f71b08
Create Date: 2026-08-16 14:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a8c3e1f4b902"
down_revision = "a9c2e4f71b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sector_faction_influence",
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sector_faction_influence", "last_action_at")
