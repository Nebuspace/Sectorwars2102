"""Additive last_activity_at on players.

QUEUE-LIVENESS-SIGNAL: a throttled, post-auth API-activity timestamp,
DISTINCT from last_game_login (which only refreshes on the login route and
is load-bearing for welcome_back()'s return-bonus detection, the retention
service's dormant/lapsed signal, and abandonment_service's 90-day
INACTIVITY_DAYS clock -- overloading that column with a multi-minute
activity touch would corrupt all three). Nullable -- absent means "no
authenticated-API activity observed yet under this signal" (e.g. every
pre-existing session until it next hits get_current_player).

Revision ID: a1f4c9e0d6b3
Revises: c8e1f2a9b4d7
Create Date: 2026-07-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a1f4c9e0d6b3"
down_revision = "c8e1f2a9b4d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "last_activity_at")
