"""add per-party appraised columns on player_trade_logs (anti-RMT windows)

ADR-0089 progressive surcharge / value caps need honest per-party appraisals
without re-pricing historical manifests. Additive nullable-default columns.

Revision ID: c9e4f1a8b2d0
Revises: b4e8c2a91f70
Create Date: 2026-08-03 16:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c9e4f1a8b2d0"
down_revision = "b4e8c2a91f70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "player_trade_logs",
        sa.Column(
            "initiator_appraised",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "player_trade_logs",
        sa.Column(
            "target_appraised",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "player_trade_logs",
        sa.Column(
            "surcharge_paid",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_player_trade_logs_created_at",
        "player_trade_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_trade_logs_created_at", table_name="player_trade_logs")
    op.drop_column("player_trade_logs", "surcharge_paid")
    op.drop_column("player_trade_logs", "target_appraised")
    op.drop_column("player_trade_logs", "initiator_appraised")
