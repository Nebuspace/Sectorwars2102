"""Backfill stale ACTIVE warp_gates.hp to the canon 10,000 pool

WO-P3-galaxy-gate-destruction. Canon: FEATURES/galaxy/warp-gates.md:329-333
("Active gate: 10,000 HP"). DATA-ONLY, scoped, idempotent -- no schema
change, no column default altered.

SCHEMA CLARIFICATION (see this WO's report -- the assigning WO described a
single table with a gate-type column; the actual schema is two SEPARATE
tables, each with its own `hp` column):
  - `warp_gate_beacons.hp` (Beacon, canon: 5,000) -- NEVER touched here.
    Always 5,000, matches its own column default, never bumped by any
    code path at any phase.
  - `warp_gates.hp` (Focus while HARMONIZING = 5,000 / Active gate once
    ACTIVE = 10,000, both canon numbers) -- its column default (5000) is
    CORRECT for a freshly-created HARMONIZING row and is NOT changed by
    this migration. `warp_gate_service.advance_gate()` already sets
    `gate.hp = 10_000` explicitly at the HARMONIZING -> ACTIVE transition
    (warp_gate_service.py, comment cites this same canon line + ADR-0011)
    -- so this migration is a ONE-TIME HISTORICAL-DATA REPAIR for any
    ACTIVE gate row that predates that code path (or was otherwise never
    bumped), not a fix to a broken default. The WHERE clause makes it a
    clean no-op if no such stale row exists.

Idempotent: re-running finds nothing left to update once applied once
(the WHERE clause only ever matches hp < 10000).
Reversible: downgrade is intentionally a no-op -- there is no reliable way
to distinguish "was legitimately 10,000 already" from "we just backfilled
it" after the fact, so reversing would risk corrupting rows this
migration never touched. (Matches this codebase's own convention for
one-way data repairs; see e.g. any prior backfill migration's downgrade.)

Revision ID: cd5752f2b45d
Revises: 86baeee81847
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cd5752f2b45d'
down_revision = '86baeee81847'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE warp_gates SET hp = 10000 "
            "WHERE status = 'ACTIVE' AND hp < 10000"
        )
    )


def downgrade() -> None:
    # Intentional no-op -- see module docstring "Reversible" section.
    pass
