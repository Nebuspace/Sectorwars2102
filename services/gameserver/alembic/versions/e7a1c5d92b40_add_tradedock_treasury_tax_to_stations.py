"""add tradedock tier, treasury, and real tax rate to stations

Foundations for the docking-slips / ship-construction / port-ownership
systems (FEATURES/economy/docking-slips, tradedock-shipyard,
port-ownership):

  - ``tradedock_tier`` — 'A' (Warp-Jumper-capable, specialized
    construction slips) or 'B' (standard construction); NULL for the
    overwhelming majority of stations that are not TradeDocks. Seeded
    by the BANG import per ADR-0041 Phase 10.5 (1 Tier-A in Terran
    Space) and by repair_tradedocks.py for pre-existing galaxies.

  - ``treasury_balance`` — the station-as-a-small-business wallet
    (port-ownership): docking fees and trade tax accrue here; owners
    withdraw from it; it transfers with the station on sale.

  - ``tax_rate`` — the trade tax actually charged on buy/sell. The
    market endpoint previously displayed a phantom
    ``getattr(station, 'tax_rate', 0.1)`` that no model column backed
    and no trade ever charged.

Revision ID: e7a1c5d92b40
Revises: d9f3b6c84a17
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a1c5d92b40'
down_revision = 'd9f3b6c84a17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'stations',
        sa.Column('tradedock_tier', sa.String(length=1), nullable=True),
    )
    op.add_column(
        'stations',
        sa.Column('treasury_balance', sa.Integer(), nullable=False,
                  server_default='0'),
    )
    op.add_column(
        'stations',
        sa.Column('tax_rate', sa.Float(), nullable=False,
                  server_default='0.10'),
    )


def downgrade() -> None:
    op.drop_column('stations', 'tax_rate')
    op.drop_column('stations', 'treasury_balance')
    op.drop_column('stations', 'tradedock_tier')
