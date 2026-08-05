"""Drop orphaned pre-Alembic Postgres enum types

WO-CLEANUP-ORPHANED-PG-ENUM-TYPES. Three Postgres enum types
(failure_type, upgrade_type, insurance_type) predate Alembic and have
zero backing columns anywhere in the current schema or migration
history (grep-verified: zero hits for any of the three names across
src/models/ and alembic/versions/). Dropping them is destructive DDL
(DROP TYPE), not additive-only — standing safety list requires Max
sign-off before the statements may run.

ENFORCEMENT (not docstring-only): ``upgrade()`` is a no-op unless
``ALEMBIC_ALLOW_ORPHAN_ENUM_DROP=1`` is set in the environment for that
``alembic upgrade`` invocation. Routine ``upgrade head`` (CI / heimdall /
stage) therefore stamps this revision without dropping anything.

If this revision is already stamped without the env flag, do NOT rely on
re-running it after Max GO — land a NEW tip migration that performs the
DROP TYPE statements (Alembic will not re-execute this revision).

Revision ID: d8b4231fd582
Revises: f7c3a9e1b850
Create Date: 2026-08-04

"""
from __future__ import annotations

import logging
import os

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "d8b4231fd582"
down_revision = "f7c3a9e1b850"
branch_labels = None
depends_on = None

_ALLOW_ENV = "ALEMBIC_ALLOW_ORPHAN_ENUM_DROP"


def upgrade() -> None:
    if os.environ.get(_ALLOW_ENV, "").strip() not in ("1", "true", "TRUE", "yes"):
        logger.warning(
            "Skipping DROP TYPE for orphan enums (failure_type/upgrade_type/"
            "insurance_type): set %s=1 after Max sign-off to apply, or land a "
            "new tip migration if this revision is already stamped. "
            "(WO-CLEANUP-ORPHAN-PG-ENUM-TYPES)",
            _ALLOW_ENV,
        )
        return

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
