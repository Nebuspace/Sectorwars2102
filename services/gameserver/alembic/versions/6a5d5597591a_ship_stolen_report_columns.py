"""Add ship stolen-report writer support columns (WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES).

Adds ``Ship.stolen_recovery_mode`` (ADR-0052 SK37 recovery-vehicle choice),
``Ship.stolen_thief_id`` (the pilot snapshotted at report-stolen time -- the
anti-collusion/auto-bounty target), ``Ship.stolen_bounty_ref`` (the JSONB
bounty entry id the auto-placed bounty lives under, so retract can locate
and remove the exact entry), and ``Ship.retract_grace_processed`` (ADR-0053
WR10 set-once 24h-grace-expired flag).

Fully additive -- four new nullable/defaulted columns only, no existing
column changes.

Revision ID: 6a5d5597591a
Revises: c4d8e61f97ab
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6a5d5597591a"
down_revision = "c4d8e61f97ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ships",
        sa.Column("stolen_recovery_mode", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column("stolen_thief_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ships_stolen_thief_id_players",
        "ships",
        "players",
        ["stolen_thief_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "ships",
        sa.Column("stolen_bounty_ref", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column(
            "retract_grace_processed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ships", "retract_grace_processed")
    op.drop_column("ships", "stolen_bounty_ref")
    op.drop_constraint("fk_ships_stolen_thief_id_players", "ships", type_="foreignkey")
    op.drop_column("ships", "stolen_thief_id")
    op.drop_column("ships", "stolen_recovery_mode")
