"""Add RADIATION_ZONE and WARP_STORM to sector_type enum (cycle-50).

Canon: SYSTEMS/sector-presence.md — hazard sector types on live Sector.type.
Extends Sector.type (models/sector.py), not orphaned SectorSpecialType.
Additive ALTER TYPE ... ADD VALUE only.

Revision ID: d7f3a1c9e842
Revises: c9e2a7f4b183
Create Date: 2026-08-10 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d7f3a1c9e842"
down_revision = "c9e2a7f4b183"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                "ALTER TYPE sector_type ADD VALUE IF NOT EXISTS 'RADIATION_ZONE'"
            )
        )
        op.execute(
            sa.text("ALTER TYPE sector_type ADD VALUE IF NOT EXISTS 'WARP_STORM'")
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE.
    pass
