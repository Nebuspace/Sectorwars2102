"""add ship_specifications.insurable

WO-SHIP-INSURANCE-CANON: moves the non-insurable-hull check off a route-level
enum set (ship_upgrades.py NON_INSURABLE_TYPES) onto a registry column
(DATA_MODELS/ships.md:22,175; FEATURES/gameplay/ship-insurance.md "Non-insurable
ships"; ADR-0029), matching the data-driven-registries convention used
elsewhere on ShipSpecification (faction_requirements, acquisition_methods).

Additive: one new NOT NULL column with a server_default (existing rows read
true without a table rewrite), plus a two-row UPDATE backfilling false onto
the two hulls canon flags non-insurable. The UPDATE touches
`ship_specifications` only — that table is seeded, boot-upserted registry
CONFIG (one row per ShipType), not player data, so this is a config backfill,
not a player-data migration. The idempotent boot seeder
(ship_specifications_seeder.py) re-asserts the same two values on every boot
going forward; this UPDATE just makes a fresh `alembic upgrade head` correct
before the seeder's next run.

Revision ID: ba1e001a8e54
Revises: 34d0fe6c1af1
Create Date: 2026-07-09 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'ba1e001a8e54'
down_revision = '34d0fe6c1af1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'ship_specifications',
        sa.Column(
            'insurable',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )
    op.execute(
        "UPDATE ship_specifications SET insurable = false "
        "WHERE type IN ('WARP_JUMPER', 'ESCAPE_POD')"
    )


def downgrade() -> None:
    op.drop_column('ship_specifications', 'insurable')
