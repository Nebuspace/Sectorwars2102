"""Add 'ANOMALY' to the sector_type enum (Audit-cycle-27 #1).

Canon: FEATURES/galaxy/generation.md — ANOMALY is a live SectorType value
(~1–2% of generated sectors, investigation-driven loot). Extends the live
``Sector.type`` column enum (models/sector.py), not the orphaned
SectorSpecialType. Additive ALTER TYPE … ADD VALUE only.

Revision ID: c9e2a7f4b183
Revises: a8bb89ee1002
Create Date: 2026-08-09 20:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9e2a7f4b183"
down_revision = "a8bb89ee1002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the migration's normal
    # transaction (a3f7c9e1d5b6 / b7e3a9d52c14 precedent) -- autocommit block.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE sector_type ADD VALUE IF NOT EXISTS 'ANOMALY'")
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE: 'ANOMALY' remains on the
    # sector_type enum after downgrade (same residue as a3f7c9e1d5b6).
    pass
