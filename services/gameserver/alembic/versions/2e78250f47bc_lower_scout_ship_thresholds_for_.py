"""lower_scout_ship_thresholds_for_balanced_gameplay

Revision ID: 2e78250f47bc
Revises: 6acc65ee7a72
Create Date: 2025-11-20 02:52:55.563904

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '2e78250f47bc'
down_revision = '6acc65ee7a72'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update Scout Ship thresholds for balanced gameplay after AI scoring fix
    # New thresholds: WEAK=0.55, AVERAGE=0.50, STRONG=0.45
    # (Previously: WEAK=0.50, AVERAGE=0.45, STRONG=0.40)
    op.execute("""
        UPDATE ship_rarity_configs
        SET
            weak_threshold = 0.55,
            average_threshold = 0.50,
            strong_threshold = 0.45
        WHERE ship_type = 'SCOUT_SHIP'
    """)


def downgrade() -> None:
    # Revert to previous thresholds
    op.execute("""
        UPDATE ship_rarity_configs
        SET
            weak_threshold = 0.50,
            average_threshold = 0.45,
            strong_threshold = 0.40
        WHERE ship_type = 'SCOUT_SHIP'
    """)