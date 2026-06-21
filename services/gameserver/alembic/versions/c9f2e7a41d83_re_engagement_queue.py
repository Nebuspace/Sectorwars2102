"""re-engagement queue (WO-RE2)

At-risk retention signals → re-engagement queue.

  - ``player_re_engagement_queue`` — one row per player flagged at-risk by the
    nightly at-risk-signal sweep (OPERATIONS/retention.md "At-risk signals").
    Records WHICH of the 7 canonical signals tripped (``signals``), the
    per-signal threshold/observed evidence (``signal_detail``), the queue
    ``status`` (OPEN | CONTACTED | RESOLVED), and the canonical day the flag was
    last (re)computed (``computed_day``) so the sweep can refresh an existing
    OPEN row in place once per canonical day.

    Additive only: a brand-new table, all columns nullable or defaulted, no
    change to any populated table. The sweep is READ-ONLY on PlayerActivity /
    PlayerSession; the only write is the upsert into this table.

Revision ID: c9f2e7a41d83
Revises: b3d8f1a92c4e
Create Date: 2026-06-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c9f2e7a41d83'
down_revision = 'b3d8f1a92c4e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'player_re_engagement_queue',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'signals',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            'signal_detail',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            'status',
            sa.String(length=20),
            nullable=False,
            server_default='OPEN',
        ),
        sa.Column(
            'computed_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('computed_day', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_player_re_engagement_queue_player_id',
        'player_re_engagement_queue',
        ['player_id'],
    )
    # The sweep upserts the single OPEN row per player; a partial unique index
    # makes "one OPEN row per player" a DB invariant (a player can have past
    # RESOLVED rows but only one live OPEN one).
    op.create_index(
        'uq_player_re_engagement_open',
        'player_re_engagement_queue',
        ['player_id'],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index(
        'ix_player_re_engagement_queue_status',
        'player_re_engagement_queue',
        ['status'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_player_re_engagement_queue_status',
        table_name='player_re_engagement_queue',
    )
    op.drop_index(
        'uq_player_re_engagement_open',
        table_name='player_re_engagement_queue',
    )
    op.drop_index(
        'ix_player_re_engagement_queue_player_id',
        table_name='player_re_engagement_queue',
    )
    op.drop_table('player_re_engagement_queue')
