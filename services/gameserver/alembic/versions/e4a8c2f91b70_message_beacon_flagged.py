"""Add message_beacons.flagged for report/hide moderation

WO-BEACON-REPORT-MODERATION v1. Additive bool default false — single
report immediately hides the beacon from denorm + direct read.

Revision ID: e4a8c2f91b70
Revises: c9e4f1a8b2d0
Create Date: 2026-08-04 00:17:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e4a8c2f91b70"
down_revision = "c9e4f1a8b2d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_beacons",
        sa.Column(
            "flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("message_beacons", "flagged")
