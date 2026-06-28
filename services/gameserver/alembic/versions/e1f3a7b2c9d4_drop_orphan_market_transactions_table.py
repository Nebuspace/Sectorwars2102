"""drop orphaned legacy market_transactions table (G8)

The ``market_transactions`` table was created by the initial-schema migration
(``c138b33baec4``) with a now-superseded shape (columns ``market_id``,
``ship_id``, ``is_purchase``, ``resource_type``, ``resource_quality``,
``price_per_unit``, ``total_price``, ``tax_paid``, ``negotiated_discount``).

It has been fully superseded by ``enhanced_market_transactions`` (the live
trade ledger that the ``MarketTransaction`` model — ``__tablename__ =
"enhanced_market_transactions"`` — actually maps to, written by the trading
routes / NPC trading / websocket service and read by the economy, ranking,
ARIA, AI-trading and port-ownership subsystems).

Verification before drop (G8):
  - NO Python model maps to ``market_transactions`` (the only model,
    ``MarketTransaction``, maps to ``enhanced_market_transactions``).
  - NO foreign key anywhere references ``market_transactions.id`` (its own
    FKs point outward at ``markets`` / ``players`` / ``ships`` only).
  - The table is CONFIRMED EMPTY (0 rows) on dev — the lead re-confirms 0 rows
    immediately before applying this migration inside the deploy window.

This drop is destructive but safe given the table is empty and orphaned.
``downgrade()`` recreates the table exactly per ``c138b33baec4`` so the
migration is fully reversible. The ``resource_type`` / ``resource_quality``
enum types are NOT dropped on upgrade nor recreated on downgrade — they are
shared with many live tables and already exist in the database; the recreate
references them with ``create_type=False``.

Revision ID: e1f3a7b2c9d4
Revises: c4e8b1f9a207
Create Date: 2026-06-22 00:00:00.000000

Re-chained at integration: down_revision was staged onto d7a2f1c9e3b5 (the head
when first drafted); re-pointed to c4e8b1f9a207 (the current sole head, after the
mining + black-market migrations) so the chain stays single-head linear.

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e1f3a7b2c9d4'
down_revision = 'c4e8b1f9a207'
branch_labels = None
depends_on = None


# Shared enum types already present in the database (used by other live
# tables). Reference with create_type=False so the recreate in downgrade()
# does not attempt to redefine an existing type.
RESOURCE_TYPE_VALUES = (
    'ORE', 'BASIC_FOOD', 'GOURMET_FOOD', 'FUEL', 'TECHNOLOGY',
    'EXOTIC_TECHNOLOGY', 'LUXURY_GOODS', 'POPULATION', 'QUANTUM_SHARDS',
    'QUANTUM_CRYSTALS', 'COMBAT_DRONES', 'PRISMATIC_ORE', 'PHOTONIC_CRYSTALS',
)
RESOURCE_QUALITY_VALUES = ('LOW', 'STANDARD', 'HIGH', 'PREMIUM', 'EXOTIC')


def upgrade() -> None:
    # Drop the orphaned, empty legacy table. No FK references it, no model
    # maps to it, so the drop is self-contained.
    op.drop_table('market_transactions')


def downgrade() -> None:
    # Recreate the table exactly as defined in the initial-schema migration
    # c138b33baec4 (fully reversible). Shared enum types are referenced with
    # create_type=False (they already exist; they are not dropped on upgrade).
    op.create_table(
        'market_transactions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'timestamp',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('market_id', sa.UUID(), nullable=False),
        sa.Column('player_id', sa.UUID(), nullable=False),
        sa.Column('ship_id', sa.UUID(), nullable=True),
        sa.Column('is_purchase', sa.Boolean(), nullable=False),
        sa.Column(
            'resource_type',
            postgresql.ENUM(
                *RESOURCE_TYPE_VALUES, name='resource_type', create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            'resource_quality',
            postgresql.ENUM(
                *RESOURCE_QUALITY_VALUES,
                name='resource_quality',
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price_per_unit', sa.Integer(), nullable=False),
        sa.Column('total_price', sa.Integer(), nullable=False),
        sa.Column('tax_paid', sa.Integer(), nullable=False),
        sa.Column('negotiated_discount', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ship_id'], ['ships.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
