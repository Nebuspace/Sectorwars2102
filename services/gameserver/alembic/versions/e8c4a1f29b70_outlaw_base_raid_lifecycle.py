"""LEG-INI-19 — OutlawBase raid lifecycle columns (npc-lodging.md).

Additive-only on ``outlaw_bases``:

- raid cooldown / last-raid identity (30-day re-raid gate + idempotency)
- combat lock holder (serialize concurrent completions)
- loot_inventory (operator-seeded cache; share fraction is config, not guessed)
- relocation_pending (placement rule remains DECISION-NEEDED)
- raid_audit_log (material outcome trail)

Revision ID: e8c4a1f29b70
Revises: a9c2e4f71b08
Create Date: 2026-08-16 16:24:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e8c4a1f29b70"
down_revision = "a9c2e4f71b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outlaw_bases",
        sa.Column("raid_cooldown_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outlaw_bases",
        sa.Column("last_raided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outlaw_bases",
        sa.Column("last_raid_completion_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "outlaw_bases",
        sa.Column("combat_lock_held_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_outlaw_bases_combat_lock_held_by",
        "outlaw_bases",
        "players",
        ["combat_lock_held_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "outlaw_bases",
        sa.Column(
            "loot_inventory",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "outlaw_bases",
        sa.Column(
            "relocation_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "outlaw_bases",
        sa.Column(
            "raid_audit_log",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "ix_outlaw_bases_raid_cooldown_until",
        "outlaw_bases",
        ["raid_cooldown_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_outlaw_bases_raid_cooldown_until", table_name="outlaw_bases")
    op.drop_column("outlaw_bases", "raid_audit_log")
    op.drop_column("outlaw_bases", "relocation_pending")
    op.drop_column("outlaw_bases", "loot_inventory")
    op.drop_constraint(
        "fk_outlaw_bases_combat_lock_held_by", "outlaw_bases", type_="foreignkey"
    )
    op.drop_column("outlaw_bases", "combat_lock_held_by")
    op.drop_column("outlaw_bases", "last_raid_completion_id")
    op.drop_column("outlaw_bases", "last_raided_at")
    op.drop_column("outlaw_bases", "raid_cooldown_until")
