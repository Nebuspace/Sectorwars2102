"""Additive priority_bumps_count on construction_reservations.

WO-P2-economy-docking-priority-bump: feeds the ConstructionReservation
queue's sort key (construction_service._sorted_queue) so a player-purchased
priority bump (FEATURES/economy/docking-slips.md:118-127 -- 5%/25%/60%/100%
of total project cost) actually advances a queued reservation ahead of
unbumped/lower-tier peers. Defaults to 0 (no bumps purchased) so every
pre-existing row is byte-unchanged in sort order.

Mirrors e7c4a1b9d602's own ADD COLUMN IF NOT EXISTS idiom for this exact
table (that migration's docstring documents src/models/construction.py's
"no Alembic migration needed" claim as a phantom-table trap on a fresh DB
that skips the create_all fallback -- this migration follows its corrected,
real-Alembic-controlled precedent, not the stale model-docstring claim).

Revision ID: bd6ad5a2ddff
Revises: a1f4c9e0d6b3
Create Date: 2026-07-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "bd6ad5a2ddff"
down_revision = "a1f4c9e0d6b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS priority_bumps_count INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE construction_reservations DROP COLUMN IF EXISTS priority_bumps_count"
    )
