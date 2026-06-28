"""market transaction tariff context (TF)

Adds two additive, nullable columns to the LIVE ledger table
``enhanced_market_transactions`` (the MarketTransaction model):

  * ``owner_tariff_rate`` (Float, nullable) — the EFFECTIVE region COMMERCE
    tariff rate in force when the trade executed (the value
    ``compute_region_tariff_multiplier`` returned for the station's region,
    already sliding-cap clamped). NULL on pre-migration rows; 0.0 when no
    tariff applied.
  * ``port_owner_id`` (UUID FK players.id, nullable, ON DELETE SET NULL) — the
    Player who owned the station at trade time. NULL for unowned/NPC stations.

These RECORD the tariff context so revenue analytics can attribute who taxed and
at what rate — they do NOT change what a trade charges. Purely additive: no
backfill, existing rows stay NULL, trades execute identically.

Additive + reversible: only ADDs nullable columns to an existing table; the
downgrade drops them cleanly (no data loss beyond the recorded context).

Single-head chaining: at author time the branch ``feat/living-npc-system`` had
exactly ONE alembic head — ``e9c3b7a1f4d2`` (team treasury transactions ledger).
This migration chains strictly onto that head so it does NOT create a spurious
independent head.

Revision ID: f1a4d8c2e7b9
Revises: e9c3b7a1f4d2
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f1a4d8c2e7b9'
down_revision = 'e9c3b7a1f4d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'enhanced_market_transactions',
        sa.Column('owner_tariff_rate', sa.Float(), nullable=True),
    )
    op.add_column(
        'enhanced_market_transactions',
        sa.Column('port_owner_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_enhanced_market_transactions_port_owner_id_players',
        'enhanced_market_transactions',
        'players',
        ['port_owner_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_enhanced_market_transactions_port_owner_id_players',
        'enhanced_market_transactions',
        type_='foreignkey',
    )
    op.drop_column('enhanced_market_transactions', 'port_owner_id')
    op.drop_column('enhanced_market_transactions', 'owner_tariff_rate')
