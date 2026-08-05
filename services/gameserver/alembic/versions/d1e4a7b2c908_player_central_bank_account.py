"""Add player_central_bank_accounts (WO-BUILD-PLAYER-CENTRAL-BANK-ACCOUNT).

Additive only: new table per ADR-0050 / DATA_MODELS/player.md
PlayerCentralBankAccount. No existing columns touched.

Revision ID: d1e4a7b2c908
Revises: c4a8e2f7b910
Create Date: 2026-08-04 19:56:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d1e4a7b2c908"
down_revision = "c4a8e2f7b910"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_central_bank_accounts",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "credits",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "commodities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ledger",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("player_central_bank_accounts")
