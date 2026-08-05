"""add players.detained_until (WO-BUILD-STATION-PROTECTION-ARREST-DETENTION)

Additive nullable DateTime — set on tractor-surrender for serious lock
reasons (stolen_ship / wanted_pilot); gates ship-access + freezes turn
regen until expiry (lazy-cleared). No backfill. No destructive change.

Revision ID: b8e2c4a91f70
Revises: f4a1c8d3e657
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = "b8e2c4a91f70"
down_revision = "f4a1c8d3e657"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("detained_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "detained_until")
