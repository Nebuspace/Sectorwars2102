"""add takeover_intents table (LEG-3639 / LEG-286 slice 1)

Region GC-subscription takeover intent — canon
sw2102-docs/DATA_MODELS/player.md § TakeoverIntent, ADR-0050 + ADR-0058 A-F3.

Creates ONE brand-new table:

  - ``takeover_intents`` — records a player's intent to take over a
    Suspended/Grace region while the PayPal payment flow runs.
    ``region_id`` FK regions.id ON DELETE CASCADE; ``caller_user_id`` FK
    users.id ON DELETE CASCADE. ``status`` is a plain String(20)
    enum-in-string (pending | won | lost | transferred | failed | expired).
    ``expires_at`` is NOT NULL with NO server_default (mandatory PayPal flow
    window, supplied by the takeover service). ``completed_at`` nullable —
    set when commit_takeover() runs.

Indexes:
  - ``(region_id, status)`` — pending intents on a region
  - ``(expires_at)`` — periodic expiry sweep
  - partial ``(region_id) WHERE status = 'pending'`` — ADR-0058 serializer

Purely **ADDITIVE / forward-only**: one new table, no change to any existing
table or row. Chained onto verified linear head ``d8e1f4a92c60`` (LEG-3599).

Revision ID: f4a8b2c91e73
Revises: d8e1f4a92c60
Create Date: 2026-09-01 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f4a8b2c91e73"
down_revision = "d8e1f4a92c60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "takeover_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "caller_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approval_url", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'won', 'lost', 'transferred', 'failed', 'expired')",
            name="valid_takeover_intent_status",
        ),
    )
    op.create_index(
        "ix_takeover_intents_region_id_status",
        "takeover_intents",
        ["region_id", "status"],
    )
    op.create_index(
        "ix_takeover_intents_expires_at",
        "takeover_intents",
        ["expires_at"],
    )
    op.create_index(
        "ix_takeover_intents_region_id_pending",
        "takeover_intents",
        ["region_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_takeover_intents_region_id_pending",
        table_name="takeover_intents",
    )
    op.drop_index(
        "ix_takeover_intents_expires_at",
        table_name="takeover_intents",
    )
    op.drop_index(
        "ix_takeover_intents_region_id_status",
        table_name="takeover_intents",
    )
    op.drop_table("takeover_intents")
