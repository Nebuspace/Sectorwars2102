"""add stations.last_price_recomputed_at + pending_price_recomputation (WO-DBB-EC4)

Per-station 1-second price-recompute rate limit (ADR-0051 SK30). Two ADDITIVE columns:
  * last_price_recomputed_at (nullable DateTime) — wall-clock anchor of the last full
    price recompute; NULL means "never recomputed" so the first call always recomputes.
  * pending_price_recomputation (NOT NULL Boolean, server_default false) — set when a
    recompute is rate-limited so the npc_scheduler flush sweep reprices the station later.

server_default false on the bool backfills existing rows on upgrade. No destructive change.

Revision ID: e8b2f6a4c1d9
Revises: d4f1a9c3e6b2
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e8b2f6a4c1d9'
down_revision = 'd4f1a9c3e6b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'stations',
        sa.Column('last_price_recomputed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'stations',
        sa.Column(
            'pending_price_recomputation',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('stations', 'pending_price_recomputation')
    op.drop_column('stations', 'last_price_recomputed_at')
