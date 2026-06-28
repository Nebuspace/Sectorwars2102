"""pending_engagements table + sectors.is_nexus_protected

Living NPC System Phase 2 (ADR-0042 police spawn-delay):

  - ``pending_engagements`` — the durable turn-counter arrival watcher.
    Squad picked at offense time; arrival gated on the offender spending
    2 turns (players.lifetime_turns_spent clock). Indexed per ADR-0042
    on (player_id, arrival_turn_threshold) and on status for the sweep.

  - ``sectors.is_nexus_protected`` — canon column
    (FEATURES/gameplay/police-forces.md "Sector protection flag"):
    flags Nexus sectors whose breach (warp-gate Phase 1 deployment,
    hostile combat) triggers the Sentinel response. Default false
    everywhere; the operator/import pipeline flags the Capital and
    Gateway Plaza cluster.

Revision ID: e5f8a7c92d46
Revises: c2d9e6f43a71
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e5f8a7c92d46'
down_revision = 'c2d9e6f43a71'
branch_labels = None
depends_on = None


ENGAGEMENT_STATUS_VALUES = ('PENDING', 'ARRIVED', 'RESOLVED', 'CANCELLED', 'EXPIRED')


def upgrade() -> None:
    op.add_column(
        'sectors',
        sa.Column(
            'is_nexus_protected',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )

    op.create_table(
        'pending_engagements',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('offense_type', sa.String(length=40), nullable=False),
        sa.Column('jurisdiction', sa.String(length=20), nullable=False),
        sa.Column('offense_sector_id', sa.Integer(), nullable=False),
        sa.Column(
            'region_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'npc_squad_ids',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column('offense_at_turn_count', sa.Integer(), nullable=False),
        sa.Column('arrival_turn_threshold', sa.Integer(), nullable=True),
        sa.Column(
            'status',
            sa.Enum(*ENGAGEMENT_STATUS_VALUES, name='engagement_status'),
            nullable=False,
            server_default='PENDING',
        ),
        sa.Column('arrival_sector_id', sa.Integer(), nullable=True),
        sa.Column('grace_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_pending_engagements_player_threshold',
        'pending_engagements',
        ['player_id', 'arrival_turn_threshold'],
    )
    op.create_index(
        'ix_pending_engagements_status',
        'pending_engagements',
        ['status'],
    )


def downgrade() -> None:
    op.drop_index('ix_pending_engagements_status', table_name='pending_engagements')
    op.drop_index('ix_pending_engagements_player_threshold', table_name='pending_engagements')
    op.drop_table('pending_engagements')
    sa.Enum(name='engagement_status').drop(op.get_bind(), checkfirst=True)
    op.drop_column('sectors', 'is_nexus_protected')
