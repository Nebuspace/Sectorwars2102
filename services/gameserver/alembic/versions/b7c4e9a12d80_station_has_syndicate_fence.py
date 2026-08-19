"""stations.has_syndicate_fence (LEG-300 Shadow Syndicate fence venues)

Additive boolean, default false. Set at bang-import worldgen on ~8% of
eligible hosts. No backfill — existing stations stay false until regen/import.

Revision ID: b7c4e9a12d80
Revises: f6b2d8a41c90
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "b7c4e9a12d80"
down_revision = "f6b2d8a41c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column(
            "has_syndicate_fence",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("stations", "has_syndicate_fence")
