"""ADR-0005: additive compound sector identity + Capital Sector.

Delivers ADR-0005's compound `(region_id, sector_number)` identity and the
per-region Capital Sector marker ADDITIVELY — the GLOBAL `sectors.sector_id`
unique key is left completely untouched (the bang import offsets each region
into a disjoint global range, and ~600 references across the codebase depend on
it). The full global-sector_id retirement is a SEPARATE future project and is
explicitly out of scope here; the two identities coexist after this migration.

What this migration does (data-preserving, idempotent, runs on dev WITH DATA):

  - Add `sectors.is_capital`            BOOLEAN NOT NULL DEFAULT false  (IF NOT EXISTS)
  - Add `regions.capital_sector_number` INTEGER                        (IF NOT EXISTS)
  - NORMALIZE `sectors.sector_number` for ALL sectors to a region-local dense
    rank: row_number() OVER (PARTITION BY region_id ORDER BY sector_id) → 1..N
    per region. This deliberately OVERWRITES any pre-existing sector_number
    values (which were historically set to the GLOBAL sector_id by the import
    glue — the bug this pass also fixes) so the new compound key is region-local
    and dense. The dense rank guarantees per-region uniqueness, so the compound
    UNIQUE added below cannot be violated by the backfill.
  - BACKFILL `is_capital` = true for each region's sector_number == 1 (the
    offset-anchor / region-local capital), false everywhere else.
  - BACKFILL `regions.capital_sector_number` = 1 (the region-local Capital
    Sector number; the bang anchor capital is region-local sector 1).
  - Add compound UNIQUE(region_id, sector_number) as
    uq_sectors_region_sector_number (guarded DO-block, safe to re-run).

Idempotent: IF NOT EXISTS guards + a constraint-existence guard make this safe
to re-run and safe on a schema that drifted ahead of its alembic pointer.

Revision ID: a7c3e1f9b264
Revises: f1a2b3c4d5e6
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op


revision = 'a7c3e1f9b264'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- 1. additive columns -----
    op.execute(
        "ALTER TABLE sectors "
        "ADD COLUMN IF NOT EXISTS is_capital BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE regions "
        "ADD COLUMN IF NOT EXISTS capital_sector_number INTEGER"
    )

    # ----- 2. normalize sector_number to a region-local dense rank (1..N) -----
    # Overwrites ALL rows uniformly (incl. any region that already had a
    # sector_number set) so the value becomes region-local. Rows with a NULL
    # region_id (defensive: legacy/orphan sectors) all fall into a single
    # PARTITION BY region_id NULL-group, so the window still produces a
    # deterministic per-bucket rank; and Postgres treats (NULL, n) as distinct
    # under the compound UNIQUE anyway, so it can never error.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY region_id
                       ORDER BY sector_id
                   ) AS rn
            FROM sectors
        )
        UPDATE sectors s
        SET sector_number = ranked.rn
        FROM ranked
        WHERE s.id = ranked.id
        """
    )

    # ----- 3. backfill is_capital: region-local sector 1 is the capital -----
    op.execute("UPDATE sectors SET is_capital = (sector_number = 1)")

    # ----- 4. backfill regions.capital_sector_number = 1 (anchor capital) -----
    op.execute(
        "UPDATE regions SET capital_sector_number = 1 "
        "WHERE capital_sector_number IS NULL"
    )

    # ----- 5. compound UNIQUE(region_id, sector_number) (guarded) -----
    # The dense-rank backfill above guarantees per-region uniqueness, so this
    # cannot violate on the data it just wrote. The global sectors.sector_id
    # unique constraint is intentionally left in place — this is additive.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_sectors_region_sector_number'
            ) THEN
                ALTER TABLE sectors
                ADD CONSTRAINT uq_sectors_region_sector_number
                UNIQUE (region_id, sector_number);
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    # Drop the compound UNIQUE and the two added columns. sector_number VALUES
    # are intentionally left as-is (region-local dense rank) — the column itself
    # predates this migration and is not owned by it.
    op.execute(
        "ALTER TABLE sectors "
        "DROP CONSTRAINT IF EXISTS uq_sectors_region_sector_number"
    )
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS capital_sector_number")
    op.execute("ALTER TABLE sectors DROP COLUMN IF EXISTS is_capital")
