"""add ship_size enum + ShipSpecification.ship_size

WO-AD (prerequisite for WO-AE Carrier ship-hangar + WO-AF Tractor Beam tow).

Adds the canonical ship-size axis to ``ship_specifications`` per
FEATURES/gameplay/ships.md "Ship size axis" (lines 318-330) and
DATA_MODELS/ships.md ShipSpecification ``size`` enum:

  - A new Postgres enum ``ship_size`` with values
    ``TINY / SMALL / MEDIUM / LARGE / CAPITAL``.
  - ``ship_specifications.ship_size`` — the new enum, **nullable**. The size
    axis drives the Carrier ship-hangar fit check (WO-AE) and the Tractor
    Beam tow per-move surcharge (WO-AF). It belongs on the spec, not the
    per-hull ``ships`` row, because every hull of a given type shares its size.

Purely **ADDITIVE / non-destructive**: a brand-new nullable column on a new
enum type. Existing ``ship_specifications`` rows are valid immediately (NULL).
No backfill UPDATE is performed here: the idempotent boot seeder
(``src/core/ship_specifications_seeder.py`` via ``main.py``) UPSERTS the
canonical size onto every player ShipType on the next boot, so the column
populates without a data migration. The two NPC-only Interdictor hulls
(``NPC_MARSHAL_INTERDICTOR`` / ``NPC_SENTINEL_INTERDICTOR``) keep NULL — canon
assigns them no size and they are never hangared or towed.

The enum is created explicitly with ``checkfirst=True`` and the column is
added with ``create_type=False`` so the type is not double-created (the
``op.add_column`` path would otherwise try to CREATE TYPE a second time).

Chained onto the active head ``f3a9c1e7b2d8`` (WO-BQ is_starport_prime) — the
single linear dev head. Does NOT branch.

Revision ID: a2f6d9b41c83
Revises: f3a9c1e7b2d8
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a2f6d9b41c83'
down_revision = 'f3a9c1e7b2d8'
branch_labels = None
depends_on = None


# Canonical ship-size axis (FEATURES/gameplay/ships.md:324-328). Mirrors the
# src.models.ship.ShipSize enum (uppercase member-name string values, the same
# convention as the existing ship_type / ship_status enums).
SHIP_SIZE_VALUES = ('TINY', 'SMALL', 'MEDIUM', 'LARGE', 'CAPITAL')

ship_size_enum = postgresql.ENUM(*SHIP_SIZE_VALUES, name='ship_size')


def upgrade() -> None:
    bind = op.get_bind()
    # Create the enum type only if it does not already exist (checkfirst) so a
    # re-run / partial-apply is safe.
    ship_size_enum.create(bind, checkfirst=True)

    # Additive nullable column. create_type=False: the type is created above,
    # so add_column must not attempt to create it again.
    op.add_column(
        'ship_specifications',
        sa.Column(
            'ship_size',
            postgresql.ENUM(*SHIP_SIZE_VALUES, name='ship_size', create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('ship_specifications', 'ship_size')
    # Drop the enum type now that no column references it (checkfirst guards a
    # double-drop on a partial-rollback).
    ship_size_enum.drop(op.get_bind(), checkfirst=True)
