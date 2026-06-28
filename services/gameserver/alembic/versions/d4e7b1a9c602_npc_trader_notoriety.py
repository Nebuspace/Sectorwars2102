"""npc trader notoriety

Adds the nullable ``notoriety`` (0-100 scruples axis) column to
``npc_characters``. Additive + nullable — pre-existing rows backfill at
scheduler startup (npc_scheduler_service._assign_trader_notoriety_sync).

Revision ID: d4e7b1a9c602
Revises: c7d1e9f3a2b8
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e7b1a9c602'
down_revision = 'c7d1e9f3a2b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'npc_characters',
        sa.Column('notoriety', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('npc_characters', 'notoriety')
