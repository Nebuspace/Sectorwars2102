"""add nullable players.research_ledger JSONB column (CRT WO-K0-2 research kernel)

CRT-MASTER §K0 / §1.2: the unified Citadel ⋈ Research ⋈ Terraform tree is read at
point-of-use and never written onto the entities it buffs, so the ENTIRE research
kernel persists in ONE additive nullable JSONB column on the existing ``players``
table. No per-system columns; the tree grows by appending catalog rows, not by
touching planet/ship/combat schema.

Purely **ADDITIVE / forward-only**: ONE nullable JSONB column. No change to any
existing row's data — every player keeps ``research_ledger = NULL`` until first
access, at which point ``research_service`` lazy-seeds the canonical default
``{rp:0, insight:0, doctrine:0, unlocked:[t.root.0]}`` in Python (NOT a DB
DEFAULT — a non-null schema default would differ from the lazy-seed contract and
would defeat the A.4 wipe+refund first-sweep detection, which keys off
``research_ledger IS NULL / swept_at absent``). No backfill, no data migration.

Chained onto the verified linear dev head ``f1a2c3d4e5b6`` (WO-CG medal effects).
Does NOT branch. Downgrade drops the single column and leaves the rest untouched.

Revision ID: a7c9e2f1b8d4
Revises: f1a2c3d4e5b6
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a7c9e2f1b8d4'
down_revision = 'f1a2c3d4e5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable JSONB — existing rows get NULL (the cold-start state) with
    # no rewrite. Lazy-seeded by research_service on first access; never a DB
    # DEFAULT (see module docstring).
    op.add_column(
        'players',
        sa.Column('research_ledger', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('players', 'research_ledger')
