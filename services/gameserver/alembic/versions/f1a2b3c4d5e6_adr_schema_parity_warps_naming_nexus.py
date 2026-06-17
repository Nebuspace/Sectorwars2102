"""ADR schema parity: galaxy config drop, warp is_latent, cluster/formation
naming uniqueness, nexus warp sector rename.

Implements four accepted ADRs as a single data-preserving, idempotent migration
that runs on a dev DB WITH DATA:

  - ADR-0006: drop galaxies.expansion_enabled and galaxies.warp_shifts_enabled
              (galaxy evolves only via region attachment; these were dead config).
  - ADR-0034: add is_latent BOOLEAN NOT NULL DEFAULT false to BOTH warp_tunnels
              and the sector_warps association table. (The WarpTunnelType.ONE_WAY
              enum value is removed at the ORM level only; directionality lives on
              is_bidirectional. No DB enum-label drop is performed because Postgres
              cannot DROP an enum label and no rows use it in worldgen — leaving
              the dormant label in the pg type is harmless and reversible.)
  - ADR-0044: add special_formations.name (String), BACKFILL it from the existing
              properties JSONB, DE-DUPLICATE within each region, then add
              UNIQUE(region_id, name) on BOTH clusters and special_formations.
  - ADR-0043: rename regions.nexus_warp_gate_sector -> nexus_warp_sector
              (data-preserving column rename).

Idempotent: uses IF EXISTS / IF NOT EXISTS and existence guards via raw SQL so it
is safe to re-run and safe on a schema that drifted ahead of its alembic pointer.

Revision ID: f1a2b3c4d5e6
Revises: e7c4a1b9d602
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op


revision = 'f1a2b3c4d5e6'
down_revision = 'e7c4a1b9d602'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----- ADR-0006: drop dead galaxy config columns -----
    op.execute("ALTER TABLE galaxies DROP COLUMN IF EXISTS expansion_enabled")
    op.execute("ALTER TABLE galaxies DROP COLUMN IF EXISTS warp_shifts_enabled")

    # ----- ADR-0034: is_latent on both warp layers -----
    op.execute(
        "ALTER TABLE warp_tunnels "
        "ADD COLUMN IF NOT EXISTS is_latent BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE sector_warps "
        "ADD COLUMN IF NOT EXISTS is_latent BOOLEAN NOT NULL DEFAULT false"
    )

    # ----- ADR-0044: special_formations.name (add -> backfill -> dedup -> UNIQUE) -----
    # 1. Add the column nullable so the table can hold existing rows.
    op.execute(
        "ALTER TABLE special_formations "
        "ADD COLUMN IF NOT EXISTS name VARCHAR(100)"
    )
    # 2. Backfill from the legacy location in properties JSONB where present.
    op.execute(
        "UPDATE special_formations "
        "SET name = NULLIF(TRIM(properties->>'name'), '') "
        "WHERE name IS NULL "
        "AND properties ? 'name' "
        "AND NULLIF(TRIM(properties->>'name'), '') IS NOT NULL"
    )
    # 3. Backfill remaining NULLs with a deterministic, type-derived placeholder
    #    (so the column is never NULL going into the dedup + constraint steps).
    #    Form: "<Type> <short-uuid>" — short-uuid keeps it unique pre-dedup.
    op.execute(
        "UPDATE special_formations "
        "SET name = type::text || ' ' || substr(id::text, 1, 8) "
        "WHERE name IS NULL OR TRIM(name) = ''"
    )
    # 4. De-duplicate within each region. For any (region_id, name) collision,
    #    keep the earliest row's name and suffix later rows with the row's short
    #    uuid so the UNIQUE constraint can be created without error.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY region_id, name
                       ORDER BY created_at, id
                   ) AS rn
            FROM special_formations
        )
        UPDATE special_formations sf
        SET name = left(sf.name, 100 - 10) || ' ' || substr(sf.id::text, 1, 8)
        FROM ranked
        WHERE sf.id = ranked.id
          AND ranked.rn > 1
        """
    )
    # 5. Add the UNIQUE constraint (guarded so re-runs are safe).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_special_formations_region_name'
            ) THEN
                ALTER TABLE special_formations
                ADD CONSTRAINT uq_special_formations_region_name
                UNIQUE (region_id, name);
            END IF;
        END$$;
        """
    )

    # ----- ADR-0044: clusters UNIQUE(region_id, name) -----
    # clusters.name is already NOT NULL. De-duplicate within each region first.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY region_id, name
                       ORDER BY created_at, id
                   ) AS rn
            FROM clusters
        )
        UPDATE clusters c
        SET name = left(c.name, 100 - 10) || ' ' || substr(c.id::text, 1, 8)
        FROM ranked
        WHERE c.id = ranked.id
          AND ranked.rn > 1
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_clusters_region_name'
            ) THEN
                ALTER TABLE clusters
                ADD CONSTRAINT uq_clusters_region_name
                UNIQUE (region_id, name);
            END IF;
        END$$;
        """
    )

    # ----- ADR-0043: rename regions.nexus_warp_gate_sector -> nexus_warp_sector -----
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'regions'
                  AND column_name = 'nexus_warp_gate_sector'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'regions'
                  AND column_name = 'nexus_warp_sector'
            ) THEN
                ALTER TABLE regions
                RENAME COLUMN nexus_warp_gate_sector TO nexus_warp_sector;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    # ----- ADR-0043: rename back -----
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'regions'
                  AND column_name = 'nexus_warp_sector'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'regions'
                  AND column_name = 'nexus_warp_gate_sector'
            ) THEN
                ALTER TABLE regions
                RENAME COLUMN nexus_warp_sector TO nexus_warp_gate_sector;
            END IF;
        END$$;
        """
    )

    # ----- ADR-0044: drop UNIQUE constraints; drop special_formations.name -----
    op.execute("ALTER TABLE clusters DROP CONSTRAINT IF EXISTS uq_clusters_region_name")
    op.execute(
        "ALTER TABLE special_formations "
        "DROP CONSTRAINT IF EXISTS uq_special_formations_region_name"
    )
    op.execute("ALTER TABLE special_formations DROP COLUMN IF EXISTS name")

    # ----- ADR-0034: drop is_latent from both warp layers -----
    op.execute("ALTER TABLE sector_warps DROP COLUMN IF EXISTS is_latent")
    op.execute("ALTER TABLE warp_tunnels DROP COLUMN IF EXISTS is_latent")

    # ----- ADR-0006: restore dropped galaxy config columns (defaulting true) -----
    op.execute(
        "ALTER TABLE galaxies "
        "ADD COLUMN IF NOT EXISTS expansion_enabled BOOLEAN NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE galaxies "
        "ADD COLUMN IF NOT EXISTS warp_shifts_enabled BOOLEAN NOT NULL DEFAULT true"
    )
