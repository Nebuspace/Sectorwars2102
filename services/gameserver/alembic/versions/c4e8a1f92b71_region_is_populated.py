"""Add regions.is_populated for generation lifecycle (LEG-2577).

Additive nullable=False with server default false. Existing central-nexus
rows are backfilled to populated=True (they predate the column).

Revision ID: c4e8a1f92b71
Revises: b7c4e9a12d80
Create Date: 2026-09-01 00:50:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "c4e8a1f92b71"
down_revision = "b7c4e9a12d80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regions",
        sa.Column(
            "is_populated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        """
        UPDATE regions
        SET is_populated = true
        WHERE name = 'central-nexus'
          AND region_type = 'central_nexus'
        """
    )


def downgrade() -> None:
    op.drop_column("regions", "is_populated")
