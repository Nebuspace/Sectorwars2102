"""Add NPCRoster.config JSONB for scheduler tuning (LEG-78).

Additive nullable JSONB — empty object default. Holds the five canon
keys from SYSTEMS/npc-scheduler.md § Configuration when operators set
per-faction/role overrides.

Revision ID: b7e2c9a14f80
Revises: a9c2e4f71b08
Create Date: 2026-08-16 14:53:00.000000
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b7e2c9a14f80"
down_revision = "a9c2e4f71b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "npc_rosters",
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("npc_rosters", "config")
