"""NPC trade attribution — enhanced_market_transactions.npc_id

Living NPC System Phase 3 (TRADER archetype, SYSTEMS/npc-lifecycle.md
§ Trade): NPC merchant captains record MarketTransaction rows exactly
like players do (full market actors); ``npc_id`` attributes those rows
(player_id stays NULL). The attribution column itself is canon-silent —
flagged in DECISIONS.md, chosen over overloading player_id.

The ADR-0062 E-V4 demand split (player_demand_score /
npc_restock_demand) lands as per-commodity keys inside the existing
``stations.commodities`` JSONB — no DDL needed; the schema-home
conflict with DATA_MODELS/economy.md (which places the split on
MarketPrice) is flagged, not silently resolved.

Deploy order: alembic upgrade BEFORE the gameserver restart that ships
this revision's code.

Revision ID: f4a2b9c81d57
Revises: e5f8a7c92d46
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f4a2b9c81d57'
down_revision = 'e5f8a7c92d46'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'enhanced_market_transactions',
        sa.Column(
            'npc_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('npc_characters.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_market_transactions_npc_id',
        'enhanced_market_transactions',
        ['npc_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_market_transactions_npc_id',
        table_name='enhanced_market_transactions',
    )
    op.drop_column('enhanced_market_transactions', 'npc_id')
