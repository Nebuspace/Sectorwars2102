"""add Ship.hangar JSONB (Carrier ship-hangar)

WO-AE — Carrier ship-hangar. Adds the storage the Carrier needs to hold whole
player ships in transit, separate from its 12-drone bay.

Per DATA_MODELS/ships.md "Carrier ship-hangar" + FEATURES/gameplay/ships.md
"Carrier hangar" (lines 332-346):

  - ``ships.hangar`` — a new **nullable** JSONB column. Only populated on
    ``capital``-size hulls (the Carrier at launch); NULL on every other ship.
    Shape (DATA_MODELS/ships.md#carrier-ship-hangar):

        {
          "capacity_units": 8,
          "docked": [
            {"ship_id", "owner_id", "size", "size_units", "docked_at",
             "request_state"}
          ]
        }

Purely **ADDITIVE / non-destructive**: a brand-new nullable column with no
default. Existing ``ships`` rows are valid immediately (NULL). No backfill: the
column is lazily initialized to the canonical 8-unit empty shape by
hangar_service the first time a Carrier accepts a dock request, so no data
migration is needed.

Chained onto the WO-AD head ``a2f6d9b41c83`` (ship_size axis), the migration
this hangar work depends on. Does NOT branch beyond that head (the WO-AD branch
is the canonical predecessor for the ship-size/hangar/tractor lane).

Revision ID: a4d8e2f91b67
Revises: a2f6d9b41c83
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a4d8e2f91b67'
down_revision = 'a2f6d9b41c83'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable JSONB. No server_default: the empty-hangar shape is an
    # application concern (hangar_service.ensure_hangar), not a DB default, so a
    # non-Carrier row legitimately stays NULL forever.
    op.add_column(
        'ships',
        sa.Column('hangar', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ships', 'hangar')
