"""Rename aria_market_intelligence.port_id -> station_id (WO-SWEEP-ARIA-MI-COLUMN).

Phantom-COLUMN class of the phantom-table disease, at column granularity:
c138b33baec4 (initial schema, station-terminology era) created
aria_market_intelligence with a column named port_id (:1620) and an unnamed
FK to stations.id (:1639) -- the station-rename era that renamed the TABLE
concept from "port" to "station" throughout the rest of the schema never
shipped the matching ALTER on THIS column. The ORM model
(models/aria_personal_intelligence.py, ARIAMarketIntelligence) has always
declared the column as station_id -- on any DB whose schema tracks alembic
history exactly (not a legacy create_all-era dev DB, where station_id may
already exist from the live model), every query
aria_personal_intelligence_service.py issues against this column raises
ProgrammingError: column "station_id" does not exist. This is what broke
the orchestrator's sweep leg 8 (every dock 500s).

NON-DESTRUCTIVE BUT NOT STRICTLY ADDITIVE: this is a column RENAME, not an
ADD -- flagged per the coordination-protocol's migration discipline (Max
sign-off requested in the implementer's STATUS for this WO). Zero rows on
stage today, but written as if data existed (a real ALTER TABLE ... RENAME
COLUMN, not a drop+recreate) since a rename is the correct operation
regardless of current row count.

GUARDED (idempotent, safe to re-run, safe on a schema that drifted ahead of
or behind its alembic pointer) -- mirrors f1a2b3c4d5e6's ADR-0043
nexus_warp_gate_sector -> nexus_warp_sector guarded-rename pattern exactly:
  - port_id present, station_id absent -> RENAME COLUMN (the expected case
    on any DB that applied c138b33baec4 as originally written).
  - station_id already present (either a legacy create_all-era DB that built
    straight from the current model, or a previous partial run of this
    migration) -> no-op, already correct.
  - NEITHER present (defensive; shouldn't happen given c138b33baec4's own
    history, but guarded per the WO's own instruction) -> ADD station_id
    UUID NULLABLE REFERENCES stations(id), plus the model's named UNIQUE
    constraint if it too is missing.

Constraint names: c138b33baec4's FK on this column (:1639) was created
UNNAMED (Postgres auto-generated aria_market_intelligence_port_id_fkey) --
the model's ForeignKey("stations.id") on station_id does not reference any
specific constraint name either, so the FK is left untouched by this
migration; a plain RENAME COLUMN keeps it fully functional against the
renamed column regardless of its own (now cosmetically stale) name. The
UNIQUE constraint IS named -- uq_player_port_commodity (:1642) -- and the
model declares it BY THAT EXACT NAME (aria_personal_intelligence.py:110,
UniqueConstraint("player_id", "station_id", "commodity",
name="uq_player_port_commodity")) despite the "port" wording, so it is
likewise left untouched: a column rename does not change a constraint's
name, and the model's expectation (a constraint named
uq_player_port_commodity covering player_id/station_id/commodity) is
already satisfied once the column itself is renamed.

Revision ID: 852befb04227
Revises: 4299dadf325b
Create Date: 2026-07-10 00:00:00.000000
"""
from alembic import op

revision = '852befb04227'
down_revision = '4299dadf325b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'port_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'station_id'
            ) THEN
                ALTER TABLE aria_market_intelligence
                RENAME COLUMN port_id TO station_id;
            ELSIF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'port_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'station_id'
            ) THEN
                -- Defensive fallback: neither column present. Add
                -- station_id fresh, matching the model's declared shape
                -- (nullable UUID FK to stations.id).
                ALTER TABLE aria_market_intelligence
                ADD COLUMN station_id UUID REFERENCES stations(id);
            END IF;
            -- station_id already present (either branch's target state,
            -- or a legacy create_all-era DB): no-op, already correct.
        END$$;
        """
    )
    # The model's named UNIQUE constraint (player_id, station_id, commodity)
    # -- add it only if genuinely missing (the defensive "neither column"
    # fallback above is the only path that could reach this table without
    # it; the normal rename path inherits c138b33baec4's original
    # uq_player_port_commodity untouched).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_player_port_commodity'
                  AND conrelid = 'aria_market_intelligence'::regclass
            ) THEN
                ALTER TABLE aria_market_intelligence
                ADD CONSTRAINT uq_player_port_commodity
                UNIQUE (player_id, station_id, commodity);
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'station_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_market_intelligence'
                  AND column_name = 'port_id'
            ) THEN
                ALTER TABLE aria_market_intelligence
                RENAME COLUMN station_id TO port_id;
            END IF;
        END$$;
        """
    )
