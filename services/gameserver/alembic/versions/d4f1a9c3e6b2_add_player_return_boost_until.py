"""add players.return_boost_until (WO-DBB-RE1)

Additive nullable DateTime — the welcome-back ×1.5 emergent-reputation window opens on a >7-day
return (set by turn_service.welcome_back) and is read by emergent_reputation_service.apply_emergent_action.
Nullable, no backfill needed (NULL = no active boost). No destructive change.

Revision ID: d4f1a9c3e6b2
Revises: c7e3f9a1b2d4
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f1a9c3e6b2'
down_revision = 'c7e3f9a1b2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('players', sa.Column('return_boost_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('players', 'return_boost_until')
