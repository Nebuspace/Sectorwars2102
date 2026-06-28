"""black-market: additive contraband columns on enhanced_market_transactions

Black-market kernel slice (audit/design-briefs/black-market.md §2):

  - ``is_illegal`` (Boolean, NULLABLE) — flags a contraband trade executed via
    ContrabandService buy/sell. NULL/False for every legal trade and every
    pre-migration row.
  - ``illegal_commodity`` (String(50), NULLABLE) — records which
    IllegalCommodity (core/illegal_commodities.py) was traded; NULL for legal
    trades.

Both columns annotate the LIVE ``enhanced_market_transactions`` ledger so the
heat/detection model and economy analytics can distinguish contraband rows
WITHOUT touching the legal supply/demand engine. Purely additive + nullable —
no existing column is removed, renamed, or altered, and no backfill is needed
(existing rows stay NULL). Fully reversible: the downgrade drops both columns.

Revision ID: c4e8b1f9a207
Revises: 7b5c0a0c93a9
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e8b1f9a207'
down_revision = '7b5c0a0c93a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable columns on the existing contraband-annotated ledger.
    op.add_column(
        'enhanced_market_transactions',
        sa.Column('is_illegal', sa.Boolean(), nullable=True),
    )
    op.add_column(
        'enhanced_market_transactions',
        sa.Column('illegal_commodity', sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('enhanced_market_transactions', 'illegal_commodity')
    op.drop_column('enhanced_market_transactions', 'is_illegal')
