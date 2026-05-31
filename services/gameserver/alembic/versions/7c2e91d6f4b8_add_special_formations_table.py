"""add special_formations table for graph-topology landmarks

Revision ID: 7c2e91d6f4b8
Revises: f4a5b6c7d8e9
Create Date: 2026-05-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '7c2e91d6f4b8'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


SPECIAL_FORMATION_VALUES = (
    'BUBBLE',
    'DEAD_END_BUBBLE',
    'GOLD_BUBBLE',
    'TUNNEL',
    'DEAD_END',
    'WARP_SINK',
    'BACKDOOR',
    'BLISTER',
    'ESCAPE_HATCH',
)


def upgrade() -> None:
    op.create_table(
        'special_formations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('region_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('regions.id', ondelete='CASCADE'), nullable=False),
        sa.Column(
            'type',
            sa.Enum(*SPECIAL_FORMATION_VALUES, name='special_formation_type'),
            nullable=False,
        ),
        sa.Column('anchor_sector_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sectors.id', ondelete='RESTRICT'), nullable=False),
        sa.Column(
            'interior_sector_ids',
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column('properties', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('is_discovered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('discovery_requirement', postgresql.JSONB(), nullable=True),
        sa.Column('generation_seed', sa.String(), nullable=True),
    )

    op.create_index('ix_special_formations_region_id', 'special_formations', ['region_id'])
    op.create_index('ix_special_formations_anchor_sector_id', 'special_formations', ['anchor_sector_id'])
    op.create_index('ix_special_formations_region_type', 'special_formations', ['region_id', 'type'])
    op.create_index(
        'ix_special_formations_interior_sector_ids',
        'special_formations',
        ['interior_sector_ids'],
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index('ix_special_formations_interior_sector_ids', table_name='special_formations')
    op.drop_index('ix_special_formations_region_type', table_name='special_formations')
    op.drop_index('ix_special_formations_anchor_sector_id', table_name='special_formations')
    op.drop_index('ix_special_formations_region_id', table_name='special_formations')
    op.drop_table('special_formations')
    sa.Enum(name='special_formation_type').drop(op.get_bind(), checkfirst=True)
