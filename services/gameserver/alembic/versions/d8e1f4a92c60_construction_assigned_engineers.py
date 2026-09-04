"""Additive assigned_engineers JSONB on construction_reservations.

LEG-3599: per-project Space Engineer assignment (up to 3 per reservation).
Defaults to [] so pre-existing rows are unchanged.

Revision ID: d8e1f4a92c60
Revises: c4e8a1f92b71
Create Date: 2026-09-01 03:50:00.000000
"""
from alembic import op


revision = "d8e1f4a92c60"
down_revision = "c4e8a1f92b71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS assigned_engineers JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE construction_reservations DROP COLUMN IF EXISTS assigned_engineers"
    )
