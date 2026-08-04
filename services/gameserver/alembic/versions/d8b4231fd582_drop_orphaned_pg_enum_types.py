"""Drop orphaned pre-Alembic Postgres enum types

WO-CLEANUP-ORPHANED-PG-ENUM-TYPES. Three Postgres enum types
(failure_type, upgrade_type, insurance_type) predate Alembic and have
zero backing columns anywhere in the current schema or migration
history (grep-verified: zero hits for any of the three names across
src/models/ and alembic/versions/). Dropping them removes dead DB
objects with no data-loss risk (no column references them, so no rows
are affected) but is a DROP DDL statement, not additive-only, hence
gated per the standing "migrations are additive only without explicit
sign-off" rule -- HELD, not applied, pending human sign-off.

Revision ID: d8b4231fd582
Revises: f7c3a9e1b850
Create Date: 2026-08-04

"""
from alembic import op


revision = "d8b4231fd582"
down_revision = "f7c3a9e1b850"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TYPE IF EXISTS failure_type")
    op.execute("DROP TYPE IF EXISTS upgrade_type")
    op.execute("DROP TYPE IF EXISTS insurance_type")


def downgrade() -> None:
    # No-op by design: Postgres requires at least one value to recreate an
    # ENUM type, and the original value sets are not recorded anywhere in
    # this migration history (these types predate Alembic). Since zero
    # columns ever referenced them, there is nothing for a downgrade to
    # restore functionally -- recreating an empty/guessed enum would be
    # worse than leaving it dropped.
    pass
