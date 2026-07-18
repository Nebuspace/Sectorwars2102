"""add unique constraint on resources type (WO-ARCH-RES-2 follow-up)

The resource registry seeder (resource_registry_seeder.py) has always
upserted one row per ResourceType via query-then-upsert (models/resource.py
docstring, WO-ARCH-RES-1-KERNEL) — application-level uniqueness was judged
sufficient at kernel time because the seed is single-threaded at startup.
This migration backs that same invariant with a real DB constraint so it
holds for ANY writer, not just the seeder.

Purely additive at the schema-shape level: one UNIQUE constraint on an
existing column, no columns added/removed/retyped.

*** APPLY PRECONDITION — READ BEFORE RUNNING ***
Adding a UNIQUE constraint requires the `resources` table to have NO
pre-existing duplicate `type` values. The seeder itself never produces
duplicates (query-then-upsert), but a live dev DB may carry a stray
duplicate row from manual testing/hand-inserts (e.g. the ORE row inserted
directly by
tests/unit/test_resource_registry.py::test_route_is_a_pure_table_read_not_a_hardcoded_list
if a test run leaked into a persistent DB rather than a rolled-back
transaction). Running `upgrade()` against a table with duplicate `type`
values will fail with a Postgres UniqueViolation — it will NOT silently
corrupt data, but it also will not fix itself.

Before applying, the operator must confirm no duplicates exist, e.g.:
    SELECT type, COUNT(*) FROM resources GROUP BY type HAVING COUNT(*) > 1;
If that returns any rows, dedupe manually (keep the most recently updated
row per type, matching the seeder's own upsert semantics) under a deploy
window BEFORE running `alembic upgrade`.

Recommendation (not implemented here): an in-migration dedupe pre-step was
considered and deliberately rejected — silently deleting rows inside a
schema migration is a destructive, hard-to-review action that belongs in an
explicit, human-visible step (verify-then-fix), not folded invisibly into
an additive migration. Keeping this migration a pure additive constraint
add means it fails loudly (UniqueViolation) rather than silently discarding
data if the precondition is violated — the Orchestrator dedupes explicitly
under its own deploy window first, per the WO.

Revision ID: 5a30b799bb25
Revises: 9381e9bf0626
Create Date: 2026-07-01 22:00:20.955151

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5a30b799bb25'
down_revision = '9381e9bf0626'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # See the APPLY PRECONDITION note above: fails with a UniqueViolation
    # (not silent data loss) if `resources.type` already has duplicates.
    op.create_unique_constraint('uq_resources_type', 'resources', ['type'])


def downgrade() -> None:
    op.drop_constraint('uq_resources_type', 'resources', type_='unique')
