"""add PRECIOUS_METALS resource_type enum value (WO-RES-PRECIOUS-METALS-SEED)

``ResourceType`` gains ``PRECIOUS_METALS`` so the resource registry seeder can
upsert a ``precious_metals`` catalog row. The column is backed by the Postgres
``resource_type`` ENUM — the Python member alone is not enough; the DB enum
must learn the value before startup seed (otherwise insert raises
``invalid input value for enum resource_type``).

Additive only: appends one enum value. ``ALTER TYPE ... ADD VALUE`` cannot run
inside a transaction, so it runs in ``op.get_context().autocommit_block()``
(mirrors e2c1f9a7b4d6 / d4f7a2c91e58 precedent). IF NOT EXISTS makes it
re-runnable. No data change, no column change.

Revision ID: d7e4a1b9c2f0
Revises: c4e17b2a95df
Create Date: 2026-08-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7e4a1b9c2f0'
down_revision = 'c4e17b2a95df'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction — autocommit_block
    # commits the (empty) migration txn, runs this outside any txn, then reopens.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE resource_type ADD VALUE IF NOT EXISTS 'PRECIOUS_METALS'")
        )


def downgrade() -> None:
    # Postgres has no safe DROP VALUE for an enum (catalog rows may reference it);
    # the appended value is left in place on downgrade (standard for ADD VALUE).
    pass
