"""add ships.quantum_harvest_cooldown_until (WO-NEBULA)

Additive only — a nullable DateTime(timezone=True) on the ships table; existing
rows backfill to NULL (no active cooldown), so this is safe on a populated table
(no destructive ALTER). The nebula harvest loop (quantum_service.harvest_nebula)
sets it to now + 2h (canonical, scaled) per attempt; per-ship per
quantum-resources.md § Harvest mechanics ("2-hour real-time per ship").

Revision ID: f2a9c4e7b1d8
Revises: e1f3a7b2c9d4
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a9c4e7b1d8'
down_revision = 'e1f3a7b2c9d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'ships',
        sa.Column('quantum_harvest_cooldown_until', sa.DateTime(timezone=True),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ships', 'quantum_harvest_cooldown_until')
