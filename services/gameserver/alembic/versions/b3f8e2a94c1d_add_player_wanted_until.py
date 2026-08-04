"""Additive wanted_until on players.

WO-BUILD-WANTED-UNTIL-TIMER: a fixed-duration auto-clear timestamp for the
black-market Severe-bust Wanted trigger (DATA_MODELS/player.md: "spec'd as
an optional auto-clear timestamp; no shipped design yet adds a fixed-
duration Wanted state for non-stolen-ship triggers" -- the stolen-ship and
reputation-recovery triggers auto-clear on their own condition, per
ranking.md#wanted-status, and never touch this column). Parallel field to
suspect_until. Nullable -- NULL means "not Wanted, or Wanted via a
condition-based trigger that doesn't use a timer".

Revision ID: b3f8e2a94c1d
Revises: ae4f2ed102fc
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b3f8e2a94c1d"
down_revision = "ae4f2ed102fc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("wanted_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "wanted_until")
