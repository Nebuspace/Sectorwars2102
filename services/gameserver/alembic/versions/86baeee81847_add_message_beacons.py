"""Add message_beacons table + Sector.message_beacons JSONB column

WO-P4-play-beacon-kernel. Canon: FEATURES/gameplay/message-beacons.md
(Status flips from Design-only to Partial on this migration). Additive
only: one new table, one nullable JSONB column on the existing `sectors`
table -- no existing column altered, no data migrated/backfilled.

Revision ID: 86baeee81847
Revises: 9210767b676a
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '86baeee81847'
down_revision = '9210767b676a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'message_beacons',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'region_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('sector_id', sa.Integer(), nullable=False),
        sa.Column(
            'deployer_player_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('deployer_nickname_at_deploy', sa.String(length=50), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('expiry', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('read_once', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('read_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column(
            'deployed_at', sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.Column('last_read_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        'idx_message_beacon_region_sector', 'message_beacons',
        ['region_id', 'sector_id'],
    )
    op.create_index(
        'idx_message_beacon_deployer', 'message_beacons',
        ['deployer_player_id', 'deployed_at'],
    )
    op.create_index(
        'idx_message_beacon_expiry', 'message_beacons', ['expiry'],
        postgresql_where=sa.text('expiry IS NOT NULL'),
    )

    op.add_column('sectors', sa.Column('message_beacons', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('sectors', 'message_beacons')
    op.drop_index('idx_message_beacon_expiry', table_name='message_beacons')
    op.drop_index('idx_message_beacon_deployer', table_name='message_beacons')
    op.drop_index('idx_message_beacon_region_sector', table_name='message_beacons')
    op.drop_table('message_beacons')
