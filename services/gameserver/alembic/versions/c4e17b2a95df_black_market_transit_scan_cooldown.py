"""Black-market transit-scan cooldown anchor on players.

Additive only: two NULLABLE columns on ``players`` backing the WO-K2
transit-side contraband scan's per-sector cooldown
(``audit/design-briefs/black-market.md`` [OPEN-9] -- "one scan per sector per
traversal, no re-roll on immediate re-entry").

Two columns rather than one because the brief's cooldown is PER-SECTOR: a lone
timestamp cannot distinguish "re-entered the sector we just scanned in" from
"arrived somewhere new".  They are written in the same transaction as the bust
they guard, which is why this is a column pair and not a Redis key -- a key
outside the move's transaction can drift from the DB when that transaction
rolls back (orchestrator ruling, 2026-08-03).

NULL/NULL on every existing row = "never scanned in transit", which makes the
roll eligible.  That is the correct default: it grants no retroactive immunity.
No backfill, no data migration, no NOT NULL, no default -- so this is safe to
apply while the stack is live and is trivially reversible.

Revision ID: c4e17b2a95df
Revises: 9c46d8ea0c11
Create Date: 2026-08-03 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e17b2a95df"
down_revision = "9c46d8ea0c11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("last_contraband_scan_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "players",
        sa.Column("last_contraband_scan_sector_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "last_contraband_scan_sector_id")
    op.drop_column("players", "last_contraband_scan_at")
