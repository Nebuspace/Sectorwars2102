"""Add aria_trading_observations table (WO-ARIA-OBS-LOG)

Per ADR-0038 (Accepted): ARIA's learning mechanism is an immutable
per-trade observation log mined by SQL aggregates and explicit heuristics
-- the genetic-algorithm framing (ARIATradingPattern's pattern_dna /
generation / fitness_score) is retired for this data (ARIATradingPattern
itself is untouched here; a separate WO owns its removal). Additive: one
new table, two new Postgres enum types, plus one nullability relaxation on
an EXISTING column --

  aria_quantum_cache.sector_id NOT NULL -> NULLABLE. ADR-0038 repurposes
  this vestigial ghost-trade-cache table as the recommendation-aggregate
  cache (aria_personal_intelligence_service.py's compute_recommendation_
  aggregates); that cache's per-player bundle has no single-sector scope,
  which the original NOT NULL sector_id (a real ghost-trade FK
  requirement) can't satisfy. Loosening only, no existing row is touched
  and no existing ghost-trade reader/writer changes behavior.

Lane C (wiring the trading.py buy/sell routes to insert observations) is
deferred to a follow-up WO -- aria_trading_observations has zero writers
until then.

Revision ID: eb772a1ab433
Revises: b601fcdaca25
Create Date: 2026-07-10 00:06:20.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'eb772a1ab433'
down_revision = 'b601fcdaca25'
branch_labels = None
depends_on = None


ACTION_VALUES = ('buy', 'sell')
OUTCOME_VALUES = ('profit', 'break_even', 'loss')


def upgrade() -> None:
    op.create_table(
        'aria_trading_observations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id'),
            nullable=False,
        ),
        sa.Column(
            'trade_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('enhanced_market_transactions.id'),
            nullable=True,
        ),
        sa.Column('commodity', sa.String(length=50), nullable=False),
        sa.Column(
            'action',
            sa.Enum(*ACTION_VALUES, name='aria_observation_action'),
            nullable=False,
        ),
        sa.Column(
            'source_station_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id'),
            nullable=False,
        ),
        sa.Column(
            'dest_station_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id'),
            nullable=True,
        ),
        # Human-readable Integer sector numbers -- NOT a FK to sectors.id
        # (UUID); mirrors enhanced_market_transactions.sector_id.
        sa.Column('source_sector_id', sa.Integer(), nullable=True),
        sa.Column('dest_sector_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Integer(), nullable=False),
        sa.Column('total_credits', sa.Integer(), nullable=False),
        sa.Column('profit', sa.Integer(), nullable=True),
        sa.Column('hours_held', sa.Float(), nullable=True),
        sa.Column(
            'outcome_classification',
            sa.Enum(*OUTCOME_VALUES, name='aria_observation_outcome'),
            nullable=True,
        ),
        sa.Column(
            'observed_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'matched_market_intel_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('aria_market_intelligence.id'),
            nullable=True,
        ),
        sa.Column(
            'recommendation_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('ai_recommendations.id'),
            nullable=True,
        ),
    )

    op.create_index(
        'idx_aria_obs_player_commodity',
        'aria_trading_observations',
        ['player_id', 'commodity'],
    )
    op.create_index(
        'idx_aria_obs_player_observed_at',
        'aria_trading_observations',
        ['player_id', 'observed_at'],
    )
    op.create_index(
        'idx_aria_obs_player_route',
        'aria_trading_observations',
        ['player_id', 'commodity', 'source_station_id', 'dest_station_id'],
    )

    op.alter_column(
        'aria_quantum_cache',
        'sector_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Reversing the nullability relaxation first would break on any
    # recommendation-aggregate cache rows already written with sector_id
    # NULL -- strip those repurposed rows before restoring NOT NULL.
    op.execute("DELETE FROM aria_quantum_cache WHERE sector_id IS NULL")
    op.alter_column(
        'aria_quantum_cache',
        'sector_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_index('idx_aria_obs_player_route', table_name='aria_trading_observations')
    op.drop_index('idx_aria_obs_player_observed_at', table_name='aria_trading_observations')
    op.drop_index('idx_aria_obs_player_commodity', table_name='aria_trading_observations')
    op.drop_table('aria_trading_observations')
    sa.Enum(name='aria_observation_outcome').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='aria_observation_action').drop(op.get_bind(), checkfirst=True)
