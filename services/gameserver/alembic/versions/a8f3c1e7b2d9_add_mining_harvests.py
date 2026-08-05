"""add mining_harvests table (WO-MINING-ASYNC-HARVEST lane a)

Persisted in-flight asteroid harvest rows so ``Ship.status = MINING`` can
span requests (FEATURES/economy/mining.md § PvP interaction). Prior kernel
set+cleared MINING in one txn; WO-MINING-PVP-INTERRUPT needs this table.

Additive only: new table + enum. No existing columns altered. Reversible
via downgrade (drop table + enum).

Revision ID: a8f3c1e7b2d9
Revises: d7e4a1b9c2f0
Create Date: 2026-08-03 15:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a8f3c1e7b2d9"
down_revision = "d7e4a1b9c2f0"
branch_labels = None
depends_on = None


HARVEST_STATUS_VALUES = ("PENDING", "COMPLETED", "CANCELLED", "INTERRUPTED")


def upgrade() -> None:
    harvest_status = postgresql.ENUM(
        *HARVEST_STATUS_VALUES,
        name="mining_harvest_status",
        create_type=False,
    )
    harvest_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "mining_harvests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ship_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sector_id", sa.Integer(), nullable=False),
        sa.Column(
            "region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            harvest_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("turns_spent", sa.Integer(), nullable=False),
        sa.Column("laser_level", sa.Integer(), nullable=False),
        sa.Column("richness_tier", sa.Integer(), nullable=False),
        sa.Column(
            "am_claimed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "has_license",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ore_yield", sa.Integer(), nullable=True),
        sa.Column("precious_metals_yield", sa.Integer(), nullable=True),
        sa.Column("quantum_shards_yield", sa.Integer(), nullable=True),
        sa.Column("am_rep_delta", sa.Integer(), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_mining_harvests_status_resolves",
        "mining_harvests",
        ["status", "resolves_at"],
    )
    op.create_index(
        "ix_mining_harvests_player",
        "mining_harvests",
        ["player_id"],
    )
    # At most one PENDING harvest per ship (interruptible in-flight window).
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_mining_harvests_ship_pending "
            "ON mining_harvests (ship_id) "
            "WHERE status = 'PENDING'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_mining_harvests_ship_pending"))
    op.drop_index("ix_mining_harvests_player", table_name="mining_harvests")
    op.drop_index(
        "ix_mining_harvests_status_resolves", table_name="mining_harvests"
    )
    op.drop_table("mining_harvests")
    sa.Enum(name="mining_harvest_status").drop(op.get_bind(), checkfirst=True)
