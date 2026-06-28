"""add CITIZEN_CLIPPER ship_type enum value (WO-GC-C)

The Galactic-Citizen hull is a new ``ShipType`` member, and ``ShipType`` is
backed by the Postgres ``ship_type`` ENUM — so the Python enum addition isn't
enough; the DB enum type must learn the value before the boot seeder can upsert
the CITIZEN_CLIPPER ``ShipSpecification`` row (otherwise the insert/select
raises ``invalid input value for enum ship_type``).

Additive only: appends one enum value. ``ALTER TYPE ... ADD VALUE`` cannot run
inside a transaction, so it runs in ``op.get_context().autocommit_block()``
(mirrors d4f7a2c91e58 / b7e3a9d52c14 precedent). IF NOT EXISTS makes it
re-runnable. No data change, no column change.

Revision ID: e2c1f9a7b4d6
Revises: d1a4f8c2e706
Create Date: 2026-06-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2c1f9a7b4d6'
down_revision = 'd1a4f8c2e706'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction — autocommit_block
    # commits the (empty) migration txn, runs this outside any txn, then reopens.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE ship_type ADD VALUE IF NOT EXISTS 'CITIZEN_CLIPPER'")
        )


def downgrade() -> None:
    # Postgres has no safe DROP VALUE for an enum (rows/specs may reference it);
    # the appended value is left in place on downgrade (standard for ADD VALUE).
    pass
