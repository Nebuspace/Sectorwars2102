"""phase 2: dormant-feature columns (region treasury + construction events)

Additive, idempotent. Unblocks features built in Phase 2 that were gated on
storage that did not exist yet (the alembic head was branched + dev drifted, so
these were deferred to this reconcile):
  - regions.treasury_balance         — region-funded TradeDock construction + the
                                        region share of port revenue.
  - construction_reservations.construction_events / pending_events (JSONB) —
                                        construction-event RNG log + decision queue.

Idempotent (ADD COLUMN IF NOT EXISTS) so it is safe even on a database whose
schema drifted ahead of its alembic pointer.

FIXED, WO-QTI-MIGRATION-CHAIN-FRESH phantom-table audit: ``construction_reservations``
itself was never created by ANY migration in this history (grepped the full
versions/ tree) -- src/models/construction.py's own docstring says "this is a
new table; Base.metadata.create_all... covers all environments -- no Alembic
migration is needed", which is exactly the phantom-table trap (a fresh DB that
skips the create_all fallback hits "relation construction_reservations does not
exist" on the ALTER below). Added a guarded catch-up ``CREATE TABLE IF NOT
EXISTS`` immediately before the ALTERs, mirroring the precedent at
f8d3a1c9e527 (npc_rosters / npc_death_log / pending_engagements catch-up). The
guard creates the table in its PRE-this-migration shape (matches this
migration's own stated purpose: construction_events/pending_events are NEW
columns being added here, so a genuinely-fresh catch-up table must NOT already
have them -- the ADD COLUMN IF NOT EXISTS calls right after add them, exactly
as they would on a DB where the table already existed via create_all). Column
shapes are a best-effort reconstruction of src/models/construction.py as it
reads today; already-migrated DBs (table already exists) see a pure no-op.

Revision ID: e7c4a1b9d602
Revises: d3f7a91c2b84
Create Date: 2026-06-16 00:00:00.000000
"""
from alembic import op


revision = 'e7c4a1b9d602'
down_revision = 'd3f7a91c2b84'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE regions ADD COLUMN IF NOT EXISTS treasury_balance INTEGER NOT NULL DEFAULT 0"
    )

    # Phantom-table catch-up (see module docstring) -- src/models/construction.py's
    # ConstructionReservation table, pre-construction_events/pending_events shape.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS construction_reservations (
            id UUID PRIMARY KEY,
            station_id UUID NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
            player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            ship_type VARCHAR(30) NOT NULL,
            state VARCHAR(30) NOT NULL DEFAULT 'requested',
            ship_name VARCHAR(100),
            total_cost INTEGER NOT NULL,
            deposit_paid INTEGER NOT NULL DEFAULT 0,
            credits_paid INTEGER NOT NULL DEFAULT 0,
            milestones JSONB NOT NULL DEFAULT '{}'::jsonb,
            resources_required JSONB NOT NULL DEFAULT '{}'::jsonb,
            resources_delivered JSONB NOT NULL DEFAULT '{}'::jsonb,
            uses_specialized_slip BOOLEAN NOT NULL DEFAULT false,
            phase_deadline TIMESTAMPTZ,
            hold_expires_at TIMESTAMPTZ,
            claim_expires_at TIMESTAMPTZ,
            rent_paid_until TIMESTAMPTZ,
            rent_owed_since TIMESTAMPTZ,
            queue_bonus_credit INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_construction_reservations_station_id "
        "ON construction_reservations(station_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_construction_reservations_player_id "
        "ON construction_reservations(player_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_construction_reservations_state "
        "ON construction_reservations(state)"
    )

    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS construction_events JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE construction_reservations "
        "ADD COLUMN IF NOT EXISTS pending_events JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    # The phantom-table catch-up CREATE TABLE added to upgrade() is
    # deliberately NOT reversed here (no DROP TABLE) -- on a DB where the
    # table pre-existed this migration (the common case: create_all-born,
    # holding real reservation rows), dropping it on downgrade would destroy
    # data this migration never owned creating. Mirrors f8d3a1c9e527's same
    # downgrade()-leaves-catch-up-tables-alone reasoning.
    op.execute("ALTER TABLE construction_reservations DROP COLUMN IF EXISTS pending_events")
    op.execute("ALTER TABLE construction_reservations DROP COLUMN IF EXISTS construction_events")
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS treasury_balance")
