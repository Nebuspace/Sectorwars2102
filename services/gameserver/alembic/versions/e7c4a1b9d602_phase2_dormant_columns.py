"""phase 2: dormant-feature columns (region treasury + construction events)

Additive, idempotent. Unblocks features built in Phase 2 that were gated on
storage that did not exist yet (the alembic head was branched + dev drifted, so
these were deferred to this reconcile):
  - regions.treasury_balance         — region-funded TradeDock construction + the
                                        region share of port revenue.
  - construction_reservations.construction_events / pending_events (JSONB) —
                                        construction-event RNG log + decision queue.

Idempotent (ADD COLUMN IF NOT EXISTS) so it is safe even on a database whose
schema drifted ahead of its alembic pointer.

Revision ID: e7c4a1b9d602
Revises: d3f7a91c2b84
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op


revision = 'e7c4a1b9d602'
down_revision = 'd3f7a91c2b84'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE regions ADD COLUMN IF NOT EXISTS treasury_balance INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS construction_events JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS pending_events JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE construction_reservations DROP COLUMN IF EXISTS pending_events")
    op.execute("ALTER TABLE construction_reservations DROP COLUMN IF EXISTS construction_events")
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS treasury_balance")
