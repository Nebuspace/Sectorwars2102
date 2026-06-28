"""add population hub flag and growth anchor to planets

Adds the two planet columns that the colonization loop still lacks:

  - ``is_population_hub`` — capital population hubs are public welcome
    worlds and never claimable (SYSTEMS/galaxy-generation.md Step 8:
    "A population hub planet (`is_population_hub = True`) ... Public,
    well-policed, non-destructible."). Backfilled to true for planets
    with population >= 1,000,000 so existing capital hubs (e.g. New
    Earth) are protected immediately.

  - ``last_growth_at`` — anchor timestamp for lazy colonist growth
    (FEATURES/planets/colonization.md "Population growth":
    colonist_rate = colonists × 0.01 × (habitability_score / 100) per
    day). NULL means growth has never been anchored; runtime code
    initializes it on first read.

NOT added here (already present in the chain — re-adding would fail):
  - ``morale`` / ``siege_turns``           -> a1b2c3d4e5f6
  - ``terraforming_active`` / ``terraforming_target`` /
    ``terraforming_start_time`` / ``terraforming_progress`` -> b2c3d4e5f6a7

Revision ID: d9f3b6c84a17
Revises: c4f1d8b27e63
Create Date: 2026-06-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9f3b6c84a17'
down_revision = 'c4f1d8b27e63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'planets',
        sa.Column(
            'is_population_hub',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'planets',
        sa.Column('last_growth_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill: capital population hubs were seeded with very large
    # populations long before this flag existed. Any planet carrying
    # >= 1,000,000 inhabitants is a capital-scale hub (covers New Earth
    # and the per-region Capital Sector hubs from galaxy-generation
    # Step 8) and must never be claimable.
    op.execute(
        "UPDATE planets SET is_population_hub = true WHERE population >= 1000000"
    )


def downgrade() -> None:
    op.drop_column('planets', 'last_growth_at')
    op.drop_column('planets', 'is_population_hub')
