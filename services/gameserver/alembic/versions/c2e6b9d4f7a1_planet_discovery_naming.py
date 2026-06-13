"""planets: discovery + naming columns (ADR-0073)

Additive nullable columns on ``planets``:
  - auto_name      generated default name (corpus)
  - custom_name    discoverer-set override
  - discovered_by  first discoverer (the only renamer; claimed or not)
  - discovered_at  when first discovered

Backfills auto_name = name for existing rows so every planet has an auto-name.

Revision ID: c2e6b9d4f7a1
Revises: b1f4c8a2d3e5
Create Date: 2026-06-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c2e6b9d4f7a1'
down_revision = 'b1f4c8a2d3e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('planets', sa.Column('auto_name', sa.String(length=100), nullable=True))
    op.add_column('planets', sa.Column('custom_name', sa.String(length=50), nullable=True))
    op.add_column('planets', sa.Column('discovered_by', postgresql.UUID(as_uuid=True),
                                       sa.ForeignKey('players.id', ondelete='SET NULL'), nullable=True))
    op.add_column('planets', sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=True))
    # Every planet gets an auto-name (preserve current display by seeding from name).
    op.execute("UPDATE planets SET auto_name = name WHERE auto_name IS NULL")


def downgrade() -> None:
    op.drop_column('planets', 'discovered_at')
    op.drop_column('planets', 'discovered_by')
    op.drop_column('planets', 'custom_name')
    op.drop_column('planets', 'auto_name')
