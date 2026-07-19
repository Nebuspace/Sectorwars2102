"""Rename aria_quantum_cache.port_id -> station_id (WO-SWEEP-QUANTUM-CACHE-COLUMN).

Second and last confirmed break from the live schema-parity run (the
authoritative census — WO-SWEEP-ARIA-MI-COLUMN's own model-vs-single-
migration diff over-claimed 5 additional tables that e86cb8130b5b had
ALREADY renamed earlier in the chain; only aria_market_intelligence
(852befb04227, this file's parent) and this table were ever actually
broken). Same phantom-COLUMN story: c138b33baec4 (initial schema) created
aria_quantum_cache with a column named port_id (:1651) and an unnamed FK to
stations.id (:1661) — e86cb8130b5b's later "rename_all_port_columns_to_
station" sweep renamed enhanced_market_transactions / market_prices /
price_history / price_alerts / markets / players.is_ported but did NOT
mention aria_quantum_cache anywhere, so this column was simply missed. The
ORM model (models/aria_personal_intelligence.py, ARIAQuantumCache) has
always declared it station_id — zero model changes needed here, the model
is the correct side.

NON-DESTRUCTIVE BUT NOT STRICTLY ADDITIVE: this is a column RENAME, not an
ADD — same flag as 852befb04227, Max sign-off requested in the
implementer's STATUS for this WO. Zero rows on stage today, but written as
if data existed (a real ALTER TABLE ... RENAME COLUMN, not a drop+
recreate).

GUARDED (idempotent, safe to re-run, safe on a schema that drifted ahead of
or behind its alembic pointer) — identical tri-guarded shape to
852befb04227 (rename / no-op-if-already-correct / defensive-add-if-
neither), itself mirroring f1a2b3c4d5e6's ADR-0043 guarded-rename pattern.

Constraint names: the FK on this column (c138b33baec4:1661) was created
UNNAMED — the model's ForeignKey("stations.id") on station_id does not
reference any specific constraint name either, so it is left untouched (a
plain RENAME COLUMN keeps it fully functional regardless of its own
cosmetically-stale name). Unlike aria_market_intelligence, this table has
NO named constraint touching this column at all — the model's two Index()
declarations (idx_quantum_cache_player_key on player_id+cache_key,
idx_quantum_cache_expiry on expires_at) neither reference station_id/
port_id, so there is nothing else to reconcile.

Revision ID: 7643ee82d04b
Revises: 852befb04227
Create Date: 2026-07-10 00:00:00.000000
"""
from alembic import op

revision = '7643ee82d04b'
down_revision = '852befb04227'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'port_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'station_id'
            ) THEN
                ALTER TABLE aria_quantum_cache
                RENAME COLUMN port_id TO station_id;
            ELSIF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'port_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'station_id'
            ) THEN
                -- Defensive fallback: neither column present. Add
                -- station_id fresh, matching the model's declared shape
                -- (nullable UUID FK to stations.id).
                ALTER TABLE aria_quantum_cache
                ADD COLUMN station_id UUID REFERENCES stations(id);
            END IF;
            -- station_id already present (either branch's target state,
            -- or a legacy create_all-era DB): no-op, already correct.
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
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'station_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'aria_quantum_cache'
                  AND column_name = 'port_id'
            ) THEN
                ALTER TABLE aria_quantum_cache
                RENAME COLUMN station_id TO port_id;
            END IF;
        END$$;
        """
    )
