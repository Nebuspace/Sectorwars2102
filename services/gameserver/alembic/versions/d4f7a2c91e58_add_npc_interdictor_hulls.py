"""add NPC Interdictor hull types and ship_specifications.is_npc_only

Police-forces slice (FEATURES/gameplay/police-forces.md "Schema impact"):

  - Extend the Postgres ``ship_type`` enum with the two NPC-only
    special-issue police hulls: ``NPC_MARSHAL_INTERDICTOR`` and
    ``NPC_SENTINEL_INTERDICTOR``. Both are filtered from every
    player-facing ship-type list at the serializer layer
    (ship_upgrades.py) — the model itself can hold them via the standard
    ``ships`` table.

  - Add ``ship_specifications.is_npc_only BOOLEAN NOT NULL DEFAULT
    false`` (canon DATA_MODELS/ships.md). When set, player-facing
    catalogs exclude the spec and ownership-transfer paths reject with
    ``ERR_NPC_ONLY_HULL``.

The spec rows themselves are seeded by the idempotent boot seeder
(src/core/ship_specifications_seeder.py via main.py) — no data
migration needed here.

ALTER TYPE ... ADD VALUE runs inside ``op.get_context().autocommit_block()``:
alembic commits the in-progress migration transaction, executes the
block's statements with AUTOCOMMIT isolation, then begins a fresh
transaction for whatever follows. (Calling
``bind.execution_options(isolation_level='AUTOCOMMIT')`` directly is
not an option — the migration transaction has already begun and
SQLAlchemy 2.x raises InvalidRequestError on a mid-transaction
isolation switch.) The ``is_npc_only`` column is added FIRST, inside
the normal transaction, so the only non-transactional work is the two
enum appends. Idempotent via IF NOT EXISTS.

Revision ID: d4f7a2c91e58
Revises: f8b2d4c61a35
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f7a2c91e58'
down_revision = 'f8b2d4c61a35'
branch_labels = None
depends_on = None


# New ship_type enum values to append (in order).
NEW_SHIP_TYPE_VALUES = ('NPC_MARSHAL_INTERDICTOR', 'NPC_SENTINEL_INTERDICTOR')


def upgrade() -> None:
    # --- ship_specifications.is_npc_only ---
    # Plain transactional DDL — runs first, inside the normal migration
    # transaction (see module docstring).
    op.add_column(
        'ship_specifications',
        sa.Column(
            'is_npc_only',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )

    # --- ship_type enum: add the two NPC-only Interdictor hulls ---
    # autocommit_block() commits the migration transaction above, runs
    # these statements outside any transaction, then opens a fresh one
    # (see module docstring for why a direct isolation switch fails).
    with op.get_context().autocommit_block():
        for value in NEW_SHIP_TYPE_VALUES:
            op.execute(
                sa.text(
                    f"ALTER TYPE ship_type ADD VALUE IF NOT EXISTS '{value}'"
                )
            )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE: the two NPC_* values
    # remain on the ship_type enum after downgrade (same residue as the
    # c4f1d8b27e63 formation-enum precedent). What CAN be cleaned up is
    # every row referencing them — destructive by necessity, mirroring
    # the f8b2d4c61a35 downgrade's JSONB + NPC-ship cleanup:

    # 1. Sector JSONB presence residue for Interdictor-piloted NPCs —
    #    strip their players_present entries (matched on the snapshotted
    #    ship_type) and drop the police squad key entirely so a
    #    downgraded database renders no ghost contacts or phantom
    #    patrols. COALESCE keeps player entries that lack a ship_type.
    #    The outer WHERE (guarded form mirroring f8b2d4c61a35's is_npc
    #    COALESCE) restricts the rewrite to rows that actually contain
    #    an NPC entry instead of rewriting every sectors row.
    op.execute(
        """
        UPDATE sectors
        SET players_present = (
            SELECT COALESCE(jsonb_agg(e), '[]'::jsonb)
            FROM jsonb_array_elements(players_present) e
            WHERE COALESCE(e->>'ship_type', '')
                  NOT IN ('NPC_MARSHAL_INTERDICTOR', 'NPC_SENTINEL_INTERDICTOR')
        )
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(players_present) npc
            WHERE COALESCE((npc->>'is_npc')::boolean, false)
        )
        """
    )
    op.execute("UPDATE sectors SET defenses = defenses - 'police_patrol_ships'")

    # 2. NPCCharacter rows piloting (or formerly piloting — KIA keeps the
    #    ship FK) an Interdictor hull. Also match on the kind-bearing
    #    bang_roster_ref the police spawner writes, in case ship_id was
    #    detached (ON DELETE SET NULL).
    op.execute(
        """
        DELETE FROM npc_characters
        WHERE ship_id IN (
            SELECT id FROM ships
            WHERE type IN ('NPC_MARSHAL_INTERDICTOR', 'NPC_SENTINEL_INTERDICTOR')
        )
        OR bang_roster_ref ~ ':(federation_marshal|marshal_captain|nexus_sentinel|sentinel_captain):'
        """
    )

    # 3. The Interdictor ship rows themselves (all NPC-piloted by
    #    construction — is_npc_only hulls never transfer to players).
    op.execute(
        """
        DELETE FROM ships
        WHERE type IN ('NPC_MARSHAL_INTERDICTOR', 'NPC_SENTINEL_INTERDICTOR')
        """
    )

    # 4. Their seeded specification rows (the boot seeder would refuse to
    #    recreate them once the enum value is unusable model-side).
    op.execute(
        """
        DELETE FROM ship_specifications
        WHERE type IN ('NPC_MARSHAL_INTERDICTOR', 'NPC_SENTINEL_INTERDICTOR')
        """
    )

    # 5. Drop the flag column.
    op.drop_column('ship_specifications', 'is_npc_only')
