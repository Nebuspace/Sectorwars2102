"""add resource registry columns (WO-ARCH-RES-1-KERNEL)

The `resources` table (models/resource.py) was vestigial — created by the
initial schema but never seeded or served by an API route. This migration
adds the catalog metadata columns the new resource_registry_seeder.py /
GET /resources route need (label, icon, category, base_price, price range,
storable/producible flags). Purely additive: eight new nullable-or-defaulted
columns on an existing, currently-empty table — no existing columns touched,
no data migrated, no constraints tightened.

Revision ID: 9381e9bf0626
Revises: 150381baa0c5
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9381e9bf0626'
down_revision = '150381baa0c5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('resources', sa.Column('label', sa.String(100), nullable=True))
    op.add_column('resources', sa.Column('icon', sa.String(50), nullable=True))
    op.add_column('resources', sa.Column('category', sa.String(50), nullable=True))
    op.create_index(op.f('ix_resources_category'), 'resources', ['category'], unique=False)
    op.add_column('resources', sa.Column('base_price', sa.Integer(), nullable=True))
    op.add_column('resources', sa.Column('price_range_min', sa.Integer(), nullable=True))
    op.add_column('resources', sa.Column('price_range_max', sa.Integer(), nullable=True))
    op.add_column('resources', sa.Column(
        'is_storable', sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.add_column('resources', sa.Column(
        'is_producible', sa.Boolean(), nullable=False, server_default=sa.false()
    ))


def downgrade() -> None:
    op.drop_column('resources', 'is_producible')
    op.drop_column('resources', 'is_storable')
    op.drop_column('resources', 'price_range_max')
    op.drop_column('resources', 'price_range_min')
    op.drop_column('resources', 'base_price')
    op.drop_index(op.f('ix_resources_category'), table_name='resources')
    op.drop_column('resources', 'category')
    op.drop_column('resources', 'icon')
    op.drop_column('resources', 'label')
