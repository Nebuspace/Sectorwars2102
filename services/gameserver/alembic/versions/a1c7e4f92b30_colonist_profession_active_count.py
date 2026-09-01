"""LEG-3679 — colonist_professions.active_count for owner assignment.

Revision ID: a1c7e4f92b30
Revises: f4a8b2c91e73
Create Date: 2026-09-01 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "a1c7e4f92b30"
down_revision = "f4a8b2c91e73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "colonist_professions",
        sa.Column("active_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("colonist_professions", "active_count")
