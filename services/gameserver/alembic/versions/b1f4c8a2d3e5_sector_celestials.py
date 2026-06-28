"""sector_celestials + sector_feature_discoveries (ADR-0073)

Persist the per-sector procedural celestial skeleton (star/belt/nebula/debris/
habitable-zone/body slots) generate-once-then-stable, plus a generalizable
per-sector hidden-feature discovery table kept separate from planet discovery.

Additive only — two new tables, no changes to existing tables.

Revision ID: b1f4c8a2d3e5
Revises: d4e7b1a9c602
Create Date: 2026-06-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b1f4c8a2d3e5'
down_revision = 'd4e7b1a9c602'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sector_celestials',
        sa.Column('sector_uuid', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('sectors.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('sector_id', sa.Integer(), nullable=False),
        sa.Column('composition', postgresql.JSONB(), nullable=False),
        sa.Column('seed', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_sector_celestials_sector_id', 'sector_celestials', ['sector_id'])

    op.create_table(
        'sector_feature_discoveries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('sector_uuid', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('sectors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature_type', sa.String(length=40), nullable=False),
        sa.Column('discovered_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('players.id', ondelete='SET NULL'), nullable=True),
        sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('sector_uuid', 'feature_type', name='uq_sector_feature'),
    )
    op.create_index('ix_sector_feature_discoveries_sector_uuid',
                    'sector_feature_discoveries', ['sector_uuid'])


def downgrade() -> None:
    op.drop_index('ix_sector_feature_discoveries_sector_uuid',
                  table_name='sector_feature_discoveries')
    op.drop_table('sector_feature_discoveries')
    op.drop_index('ix_sector_celestials_sector_id', table_name='sector_celestials')
    op.drop_table('sector_celestials')
