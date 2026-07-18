"""Additive intrasystem_pose JSONB on players + npc_characters.

WO-ISP-1: authoritative in-system x/y/heading/leg plan for multiplayer sync
and reload stability. Nullable JSONB — absent means "no pose yet" (client/
server seed a resting anchor on first burn or sector entry).

Revision ID: c8e1f2a9b4d7
Revises: 09d0c6e55927
Create Date: 2026-07-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c8e1f2a9b4d7"
down_revision = "09d0c6e55927"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("intrasystem_pose", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "npc_characters",
        sa.Column("intrasystem_pose", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("npc_characters", "intrasystem_pose")
    op.drop_column("players", "intrasystem_pose")
