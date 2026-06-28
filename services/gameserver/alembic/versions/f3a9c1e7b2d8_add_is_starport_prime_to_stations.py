"""add is_starport_prime to stations

Adds the Central Nexus Starport Prime discriminator column to the stations
table. Both Starport Prime and a regional Capital station are
StationClass.CLASS_0, but their docking-slip pools differ (200/50 vs 80/30 per
FEATURES/economy/docking-slips). This nullable/defaulted boolean lets
docking_service tell them apart. Purely additive (new column, default False) —
non-destructive on populated tables.

Chained onto the active head c5a8e2f1b9d3 (ADR-0059 governance schema), the line
the dev DB was reconciled onto on 2026-06-16. The terraforming side branch head
(b2c3d4e5f6a7) is a pre-existing orphan branch and is intentionally NOT touched.

Revision ID: f3a9c1e7b2d8
Revises: c5a8e2f1b9d3
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a9c1e7b2d8'
down_revision = 'c5a8e2f1b9d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'stations',
        sa.Column(
            'is_starport_prime',
            sa.Boolean(),
            nullable=False,
            server_default='false',
        ),
    )


def downgrade() -> None:
    op.drop_column('stations', 'is_starport_prime')
