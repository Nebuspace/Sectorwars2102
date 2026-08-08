"""Drop orphaned legacy PG enum types (WO-CLEANUP-ORPHANED-PG-ENUM-TYPES).

failure_type, upgrade_type, insurance_type were hand-created by the old
start.sh create_all fallback (removed e4d5c50e, WO-INFRA-CREATEALL-RETIRE)
via a manual CREATE TYPE block, never through the migration chain. Zero
model columns or migrations reference any of the three -- confirmed via
grep across src/models/ and alembic/versions/. Safe on any DB: a fresh
DB never had them (no create_all fallback ran), and a DB that did run
the old fallback has them sitting unused with nothing depending on them.

Revision ID: e2c7a4f91b3d
Revises: 81be1c9f25e6
Create Date: 2026-08-04 21:05:00.000000

"""
from alembic import op


revision = "e2c7a4f91b3d"
down_revision = "81be1c9f25e6"
branch_labels = None
depends_on = None

_ORPHANED_ENUM_TYPES = ("failure_type", "upgrade_type", "insurance_type")


def upgrade() -> None:
    for enum_type in _ORPHANED_ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {enum_type}")


def downgrade() -> None:
    # No-op: these types backed zero columns, so there is nothing to
    # recreate that any table could reference.
    pass
