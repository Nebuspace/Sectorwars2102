"""ship-mods slot grid columns (additive nullable JSONB)

Canon: audit/design-briefs/ship-mods-unified/SHIP-MODS-MASTER.md §9
(Max-blessed NO-CANON kernel) — WO-SM-1.

Adds two additive, nullable JSONB columns for the SHIP-MODS slot grid:

  * ``ships.modules``               (§9.1) — per-instance installed modules;
                                     stays NULL until the first module install.
  * ``ship_specifications.module_slots`` (§9.2) — per-type slot lattice;
                                     seeded per ShipType by the idempotent boot
                                     upserter in ship_specifications_seeder.py.

Additive only — two new nullable columns, NO backfill, NO destructive op.
Existing rows are valid pre-seed (NULL = "no modules" / "hull predates the
feature → no grid yet"). The boot upserter populates module_slots at startup;
this migration only creates the columns. The fresh ``ships.modules`` column
sits BESIDE ``ships.equipment_slots`` and never touches the ADR-0030
sensor/slipdrive/special_equipment keys that live there.

Revision ID: b3d8f1a92c4e
Revises: f4a9c7e21b6d
Create Date: 2026-06-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b3d8f1a92c4e'
down_revision = 'f4a9c7e21b6d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # §9.1 — per-instance installed modules (NULL until first install).
    op.add_column(
        'ships',
        sa.Column('modules', postgresql.JSONB(), nullable=True),
    )
    # §9.2 — per-type slot lattice (NULL until the boot upserter seeds it).
    op.add_column(
        'ship_specifications',
        sa.Column('module_slots', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ship_specifications', 'module_slots')
    op.drop_column('ships', 'modules')
