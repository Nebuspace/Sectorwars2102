"""add route_optimization_runs table (WO-SB-RO2)

Route-optimization telemetry — run-log for both player-facing optimizer
endpoints (POST /api/v1/routes/optimize and POST /api/v1/ai/optimize-route),
feeding the admin NH18 RouteOptimizationDisplay real data instead of an
honest-empty stub.

Purely ADDITIVE / forward-only: one new table, no change to any existing
table or row. Chained onto the verified dev head ``5a30b799bb25``
(add_unique_constraint_on_resources_type). Does NOT branch. Downgrade drops
the table (indexes first) and leaves the rest of the schema untouched.

Revision ID: cdea102d345b
Revises: 5a30b799bb25
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'cdea102d345b'
down_revision = '5a30b799bb25'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'route_optimization_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('objective', sa.String(length=32), nullable=False),
        sa.Column('start_sector', sa.String(length=64), nullable=False),
        sa.Column('end_sector', sa.String(length=64), nullable=True),
        sa.Column('sectors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('total_profit', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_distance', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_time_hours', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('cargo_efficiency', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('route_confidence', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='completed'),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        'ix_route_optimization_runs_player_id',
        'route_optimization_runs',
        ['player_id'],
    )
    op.create_index(
        'ix_route_optimization_runs_created_at',
        'route_optimization_runs',
        ['created_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_route_optimization_runs_created_at',
        table_name='route_optimization_runs',
    )
    op.drop_index(
        'ix_route_optimization_runs_player_id',
        table_name='route_optimization_runs',
    )
    op.drop_table('route_optimization_runs')
