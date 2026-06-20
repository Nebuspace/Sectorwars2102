"""add Ship.tow_state JSONB (Tractor Beam tow operations)

WO-AF — Tractor Beam tow operations. Adds the storage the HAULER needs to hold
its active tow lock (the ship it is currently towing), separate from the Carrier
ship-hangar (WO-AE) and from regular cargo.

Per DATA_MODELS/ships.md "Ship tow state" + FEATURES/gameplay/ships.md
"Tractor Beam tow operations" (lines 348-371) + ADR-0067
(group-e-tractor-tow-quantum-jump):

  - ``ships.tow_state`` — a new **nullable** JSONB column. Populated only on the
    HAULER while a tow is active; NULL on every other ship (the default). Shape
    (DATA_MODELS/ships.md#ship-tow-state):

        {
          "towed_ship_id": "uuid",
          "towed_owner_id": "uuid",
          "towed_size": "tiny | small | medium | large",
          "surcharge_per_move": 1,
          "locked_at": "iso8601",
          "lock_sector_id": "integer"
        }

    ``surcharge_per_move`` is cached at lock-on from ``towed_size`` (tiny+1 /
    small+2 / medium+3 / large+5) so the movement service doesn't re-traverse
    ShipSpecification on every move.

Purely **ADDITIVE / non-destructive**: a brand-new nullable column with no
default. Existing ``ships`` rows are valid immediately (NULL — no tow active).
No backfill: the column is set by tow_service only on a successful lock-on and
cleared on detach / detach-on-destruction.

Chained onto the WO-AE head ``a4d8e2f91b67`` (Ship.hangar), the verified current
dev head for the ship-size / hangar / tractor lane (WO-AD → WO-AE → WO-AF). Does
NOT branch beyond that head.

Revision ID: b7e1d4f92a38
Revises: a4d8e2f91b67
Create Date: 2026-06-20 15:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b7e1d4f92a38'
down_revision = 'a4d8e2f91b67'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable JSONB. No server_default: an absent tow is NULL, not an
    # empty shape — tow_state is set by tow_service only on a live lock-on.
    op.add_column(
        'ships',
        sa.Column('tow_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ships', 'tow_state')
