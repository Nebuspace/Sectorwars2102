"""Add region_id_snapshot to remaining SK24 audit tables (ADR-0050).

combat_logs already has the column (2d61e3b17ddd). This migration covers the
other five named audit surfaces:

- enhanced_market_transactions
- bounty_claims
- npc_death_log
- aria_trading_observations  (ADR-0050 sketch name: aria_observation_log)
- pirate_kill_log

Each column is a plain UUID snapshot WITHOUT an FK to regions.id — same
contract as CombatLog.region_id_snapshot — so region terminate/regenerate
cannot cascade-delete or SET-NULL the audit trail's region identity.

pirate_kill_log.region_id was ON DELETE CASCADE (would wipe kill history on
region delete). Softened to SET NULL + nullable so the live FK can clear while
region_id_snapshot preserves the audit identity.

No backfill of historical rows (same as the combat_logs WO).

Revision ID: b3e8c1a94d70
Revises: a1c9e4f72b06
Create Date: 2026-08-05 18:05:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b3e8c1a94d70"
down_revision = "a1c9e4f72b06"
branch_labels = None
depends_on = None

_TABLES = (
    "enhanced_market_transactions",
    "bounty_claims",
    "npc_death_log",
    "aria_trading_observations",
    "pirate_kill_log",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "region_id_snapshot",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )

    # Soften pirate_kill_log.region_id so region delete does not wipe rows.
    # Constraint name from create migration — look up if rename happened.
    bind = op.get_bind()
    fk_name = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'pirate_kill_log'::regclass "
            "AND contype = 'f' "
            "AND pg_get_constraintdef(oid) LIKE '%region_id%'"
        )
    ).scalar()
    if fk_name:
        op.drop_constraint(fk_name, "pirate_kill_log", type_="foreignkey")
    op.alter_column(
        "pirate_kill_log",
        "region_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        "pirate_kill_log_region_id_fkey",
        "pirate_kill_log",
        "regions",
        ["region_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Best-effort restore: backfill live FK from snapshot where possible.
    op.execute(
        "UPDATE pirate_kill_log SET region_id = region_id_snapshot "
        "WHERE region_id IS NULL AND region_id_snapshot IS NOT NULL"
    )
    op.drop_constraint(
        "pirate_kill_log_region_id_fkey", "pirate_kill_log", type_="foreignkey"
    )
    # Rows still NULL block NOT NULL restore — delete orphans rather than
    # inventing a sentinel UUID.
    op.execute("DELETE FROM pirate_kill_log WHERE region_id IS NULL")
    op.alter_column(
        "pirate_kill_log",
        "region_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "pirate_kill_log_region_id_fkey",
        "pirate_kill_log",
        "regions",
        ["region_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for table in reversed(_TABLES):
        op.drop_column(table, "region_id_snapshot")
