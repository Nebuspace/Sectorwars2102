"""LEG-3994 — player direct relationships + contract posting blocks.

Additive tables for contracts.md § Anti-griefing Status #3:
- player_direct_relationships: pairwise reputation + is_blocked
- contract_posting_blocks: platform POST suspension

Revision ID: c3e8f1a92b70
Revises: b2d8f4a91c70
Create Date: 2026-09-03 00:50:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c3e8f1a92b70"
down_revision = "b2d8f4a91c70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_direct_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("viewer_player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reputation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["viewer_player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "viewer_player_id",
            "subject_player_id",
            name="uq_player_direct_relationships_viewer_subject",
        ),
    )
    op.create_index(
        "ix_player_direct_relationships_viewer_player_id",
        "player_direct_relationships",
        ["viewer_player_id"],
    )
    op.create_index(
        "ix_player_direct_relationships_subject_player_id",
        "player_direct_relationships",
        ["subject_player_id"],
    )

    op.create_table(
        "contract_posting_blocks",
        sa.Column("player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id"),
    )


def downgrade() -> None:
    op.drop_table("contract_posting_blocks")
    op.drop_index(
        "ix_player_direct_relationships_subject_player_id",
        table_name="player_direct_relationships",
    )
    op.drop_index(
        "ix_player_direct_relationships_viewer_player_id",
        table_name="player_direct_relationships",
    )
    op.drop_table("player_direct_relationships")
