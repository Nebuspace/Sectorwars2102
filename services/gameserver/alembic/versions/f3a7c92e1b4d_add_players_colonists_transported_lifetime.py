"""add players.colonists_transported_lifetime (WO-PC1)

Durable lifetime counter of colonists a player has LANDED onto planets (claim
founding + disembark transfers). Drives the pioneer_office_pillar medal
(@10,000, medal_catalog trigger "colonists_transported_lifetime").

One ADDITIVE column:
  * colonists_transported_lifetime (NOT NULL Integer, server_default '0') —
    server_default backfills every existing player row to 0 on upgrade, so the
    column is non-null without a data-migration pass. No destructive change.

Revision ID: f3a7c92e1b4d
Revises: e8b2f6a4c1d9
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a7c92e1b4d'
down_revision = 'e8b2f6a4c1d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'players',
        sa.Column(
            'colonists_transported_lifetime',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    op.drop_column('players', 'colonists_transported_lifetime')
