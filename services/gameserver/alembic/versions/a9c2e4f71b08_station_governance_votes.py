"""station_governance_votes — syndicate policy motions (LEG-301)

Additive table only. Canon: FEATURES/economy/port-ownership.md vote-threshold
table. Revises NPC lodging head (current alembic head on feat).

Revision ID: a9c2e4f71b08
Revises: f6b2d8a41c90
Create Date: 2026-08-19 20:40:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a9c2e4f71b08"
down_revision = "f6b2d8a41c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "station_governance_votes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "station_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vote_type", sa.String(32), nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("share_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ballots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rng_seed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_station_governance_votes_station_id",
        "station_governance_votes",
        ["station_id"],
    )
    op.create_index(
        "ix_station_governance_votes_vote_type",
        "station_governance_votes",
        ["vote_type"],
    )
    op.create_index(
        "ix_station_governance_votes_status",
        "station_governance_votes",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_station_governance_votes_status", table_name="station_governance_votes")
    op.drop_index("ix_station_governance_votes_vote_type", table_name="station_governance_votes")
    op.drop_index("ix_station_governance_votes_station_id", table_name="station_governance_votes")
    op.drop_table("station_governance_votes")
