"""LEG-2253 — colonist professions kernel tables.

Revision ID: f3a9c2e81d40
Revises: e8c4a1f29b70
Create Date: 2026-08-28 03:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f3a9c2e81d40"
down_revision = "e8c4a1f29b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "colonist_professions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("planet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("planets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profession", sa.String(length=40), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("planet_id", "profession", name="uq_colonist_profession_planet_profession"),
    )
    op.create_index("ix_colonist_professions_planet_id", "colonist_professions", ["planet_id"])

    op.create_table(
        "profession_training_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("planet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("planets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profession", sa.String(length=40), nullable=False),
        sa.Column("trainee_count", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
    )
    op.create_index("ix_profession_training_queue_planet_id", "profession_training_queue", ["planet_id"])
    op.create_index("ix_profession_training_queue_owner_player_id", "profession_training_queue", ["owner_player_id"])


def downgrade() -> None:
    op.drop_index("ix_profession_training_queue_owner_player_id", table_name="profession_training_queue")
    op.drop_index("ix_profession_training_queue_planet_id", table_name="profession_training_queue")
    op.drop_table("profession_training_queue")
    op.drop_index("ix_colonist_professions_planet_id", table_name="colonist_professions")
    op.drop_table("colonist_professions")
