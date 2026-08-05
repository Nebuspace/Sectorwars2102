"""Add 'abandoned' to the registry_event_type enum
(WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON).

The relinquish half of "Abandonment" (ship-registry.md) -- the owner giving
up ownership with no new owner yet. The later free-claim (a new owner taking
possession) reuses the existing 'ownership_transfer' value, matching every
other ownership-change event in this enum. ``Ship.is_abandoned`` /
``Ship.abandoned_at`` already exist (additive, shipped by an earlier WO) --
no ship-table column changes here.

Revision ID: a3f7c9e1d5b6
Revises: d1e9f4a6b23c
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a3f7c9e1d5b6"
down_revision = "d1e9f4a6b23c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the migration's normal
    # transaction (b7e3a9d52c14 / 7b5c0a0c93a9 precedent) -- autocommit block.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE registry_event_type ADD VALUE IF NOT EXISTS 'abandoned'")
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE: 'abandoned' remains on the
    # registry_event_type enum after downgrade (same residue as the
    # b7e3a9d52c14 precedent).
    pass
