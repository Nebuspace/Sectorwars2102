"""add players.last_distress_at (WO-GWQ-STRANDING)

Additive nullable DateTime -- the Federation distress-beacon 24h scaled
cooldown anchor (FEATURES/gameplay/factions-and-teams.md#reputation-triggers:
"Use the Federation distress beacon ... 24-hour cooldown"). Set by
distress_service.use_distress_beacon to the fire time; the cooldown deadline
is derived at read time via scaled_deadline(24, start=last_distress_at).
Nullable, no backfill needed (NULL = never used, beacon available). No
destructive change.

Revision ID: 8b9aa2bd781d
Revises: fea17cc334a8
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b9aa2bd781d'
down_revision = 'fea17cc334a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('players', sa.Column('last_distress_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('players', 'last_distress_at')
