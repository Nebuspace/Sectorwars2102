"""LEG-3961 — player_npc_encounters co-presence table.

Revision ID: b2d8f4a91c70
Revises: a1c7e4f92b30
Create Date: 2026-09-02 10:56:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b2d8f4a91c70"
down_revision = "a1c7e4f92b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_npc_encounters",
        sa.Column("player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("npc_character_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "last_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_sector_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["npc_character_id"], ["npc_characters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("player_id", "npc_character_id"),
    )
    op.create_index(
        "ix_player_npc_encounters_player_id",
        "player_npc_encounters",
        ["player_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_npc_encounters_player_id", table_name="player_npc_encounters")
    op.drop_table("player_npc_encounters")
