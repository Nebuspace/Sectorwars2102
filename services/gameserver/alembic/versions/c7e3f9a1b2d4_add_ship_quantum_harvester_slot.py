"""add ships.quantum_harvester_slot bool (WO-DBB-QR1)

Additive only — a nullable=False Boolean with server_default false, so existing ship rows backfill
to false safely on a populated table (no destructive ALTER). The Quantum Harvester equip flow
(ship_upgrade_service install/uninstall) flips it; prereq for QR2.

Revision ID: c7e3f9a1b2d4
Revises: b3d8f1a4c9e2
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7e3f9a1b2d4'
down_revision = 'b3d8f1a4c9e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'ships',
        sa.Column('quantum_harvester_slot', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('ships', 'quantum_harvester_slot')
