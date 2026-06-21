"""add clusters structured nebula fields (WO-DBB-QR4)

Persist the cluster's dominant/representative nebula as STRUCTURED columns
(previously nebula data lived only as per-sector special_feature strings).

The bang payload carries nebula data PER-SECTOR only (sector.nebula =
{type, density}); there is no cluster-level nebula block. bang_import_service
derives each cluster's representative nebula from its member sectors:
  * nebula_type             = the most common nebula type among the cluster's
                              nebula sectors
  * quantum_field_strength  = the mean density of those sectors (the only
                              quantitative nebula attribute the payload carries)
  * color_hex               = no payload source yet → always NULL for now

Three ADDITIVE NULLABLE columns (a cluster with no nebula sectors leaves all
three NULL; no data-migration pass, no destructive change):
  * nebula_type             (String(50), nullable)
  * quantum_field_strength  (Float,      nullable)
  * color_hex               (String(20), nullable)

Revision ID: c5a9f1e6b34d
Revises: f3a7c92e1b4d
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5a9f1e6b34d'
down_revision = 'f3a7c92e1b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'clusters',
        sa.Column('nebula_type', sa.String(length=50), nullable=True),
    )
    op.add_column(
        'clusters',
        sa.Column('quantum_field_strength', sa.Float(), nullable=True),
    )
    op.add_column(
        'clusters',
        sa.Column('color_hex', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('clusters', 'color_hex')
    op.drop_column('clusters', 'quantum_field_strength')
    op.drop_column('clusters', 'nebula_type')
