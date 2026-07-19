"""Additive npc_id on docking_slip_occupancies (WO-P9-realtime-npc-trader-slips).

Gives TRADER-archetype NPCs real docking-slip occupancy through the SAME
table the player dock path uses (npc-traders.md § Market participation --
"traders occupy real docking slips like players"), rather than a parallel
occupancy store. Mirrors the player_id/npc_id dual-nullable-FK pattern
already established on MarketTransaction for the same NPC-attribution need.

player_id relaxes from NOT NULL to nullable (widening, not destructive --
every existing row already has player_id set and satisfies the new CHECK
constraint unchanged) so an NPC-owned row can carry player_id=NULL /
npc_id=<uuid> instead. `(player_id IS NOT NULL) != (npc_id IS NOT NULL)`
enforces exactly one owner per row at the DB level.

Idempotent by design, matching bd6ad5a2ddff's corrected precedent for this
exact "no Alembic migration is needed" phantom-table class (docking.py's own
module docstring makes the same now-stale claim `docking_slip_occupancies`
was created via startup create_all only): ADD COLUMN IF NOT EXISTS is a
no-op on a fresh DB where 9f1e216e2321's phantom-table catchup already
created the table from the CURRENT (post-this-change) model definition, and
a real ALTER on every already-existing dev/stage DB that predates this
column. The CHECK constraint is guarded via pg_constraint lookup for the
same reason (ADD CONSTRAINT has no native IF NOT EXISTS in Postgres).

Revision ID: f2adaac4162b
Revises: bd6ad5a2ddff
Create Date: 2026-07-17 00:00:00.000000
"""
from alembic import op


revision = "f2adaac4162b"
down_revision = "bd6ad5a2ddff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE docking_slip_occupancies ALTER COLUMN player_id DROP NOT NULL"
    )
    op.execute(
        "ALTER TABLE docking_slip_occupancies "
        "ADD COLUMN IF NOT EXISTS npc_id UUID UNIQUE "
        "REFERENCES npc_characters(id) ON DELETE CASCADE"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_docking_slip_occupancy_exactly_one_owner'
            ) THEN
                ALTER TABLE docking_slip_occupancies
                ADD CONSTRAINT ck_docking_slip_occupancy_exactly_one_owner
                CHECK ((player_id IS NOT NULL) != (npc_id IS NOT NULL));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE docking_slip_occupancies "
        "DROP CONSTRAINT IF EXISTS ck_docking_slip_occupancy_exactly_one_owner"
    )
    op.execute(
        "ALTER TABLE docking_slip_occupancies DROP COLUMN IF EXISTS npc_id"
    )
    # Best-effort: fails loudly (as it should) if any row has player_id NULL
    # at downgrade time -- i.e. a live NPC-held slip -- since that data can
    # no longer be represented once npc_id is gone. Not silently patched;
    # an operator downgrading through live NPC occupancy needs to see this.
    op.execute(
        "ALTER TABLE docking_slip_occupancies ALTER COLUMN player_id SET NOT NULL"
    )
