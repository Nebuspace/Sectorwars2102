"""Add stations.region_assignment_role for SpaceDock anchors

WO-ANCHOR-REPAIR-SERVICE thin v1. Additive nullable string — distinguishes
starter vs frontier SpaceDock anchors without heuristic inference.
Unset (NULL) means role unknown; detect-only scan treats that as
unverifiable rather than false-missing.

Revision ID: f7c3a9e1b850
Revises: e4a8c2f91b70
Create Date: 2026-08-04 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f7c3a9e1b850"
down_revision = "e4a8c2f91b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column("region_assignment_role", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stations", "region_assignment_role")
