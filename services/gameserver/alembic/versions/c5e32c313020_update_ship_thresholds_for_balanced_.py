"""update_ship_thresholds_for_balanced_gameplay

Revision ID: c5e32c313020
Revises: fe22441146b1
Create Date: 2025-11-17 23:11:36.221036

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c5e32c313020'
down_revision = 'fe22441146b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Update ship persuasion thresholds to new balanced values.

    The original thresholds were too harsh, causing most players to fail First Login
    even with reasonable responses. These new values provide better game balance:
    - Players with good stories can claim mid-tier ships
    - Escape Pod remains easy to claim
    - Rare ships still require excellent performance
    """

    # ESCAPE_POD - Keep easy (tier 1)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.3, average_threshold = 0.3, strong_threshold = 0.3
        WHERE ship_type = 'ESCAPE_POD';
    """)

    # LIGHT_FREIGHTER - Easier to claim (tier 2)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.4, average_threshold = 0.35, strong_threshold = 0.3
        WHERE ship_type = 'LIGHT_FREIGHTER';
    """)

    # SCOUT_SHIP - Moderate difficulty (tier 3)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.5, average_threshold = 0.45, strong_threshold = 0.4
        WHERE ship_type = 'SCOUT_SHIP';
    """)

    # FAST_COURIER - Moderate-high difficulty (tier 3)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.55, average_threshold = 0.5, strong_threshold = 0.45
        WHERE ship_type = 'FAST_COURIER';
    """)

    # CARGO_HAULER - High value ship (tier 4)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.65, average_threshold = 0.55, strong_threshold = 0.5
        WHERE ship_type = 'CARGO_HAULER';
    """)

    # DEFENDER - Very rare combat ship (tier 5)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.75, average_threshold = 0.65, strong_threshold = 0.6
        WHERE ship_type = 'DEFENDER';
    """)

    # COLONY_SHIP - Extremely rare (tier 6)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.8, average_threshold = 0.7, strong_threshold = 0.65
        WHERE ship_type = 'COLONY_SHIP';
    """)

    # CARRIER - Ultra rare (tier 7)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.85, average_threshold = 0.75, strong_threshold = 0.7
        WHERE ship_type = 'CARRIER';
    """)


def downgrade() -> None:
    """
    Restore original harsh thresholds (for rollback if needed).

    Note: These were the original values that proved too difficult.
    """

    # ESCAPE_POD - Restore original
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.3, average_threshold = 0.3, strong_threshold = 0.3
        WHERE ship_type = 'ESCAPE_POD';
    """)

    # LIGHT_FREIGHTER - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.7, average_threshold = 0.6, strong_threshold = 0.5
        WHERE ship_type = 'LIGHT_FREIGHTER';
    """)

    # SCOUT_SHIP - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.8, average_threshold = 0.7, strong_threshold = 0.6
        WHERE ship_type = 'SCOUT_SHIP';
    """)

    # FAST_COURIER - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.85, average_threshold = 0.75, strong_threshold = 0.65
        WHERE ship_type = 'FAST_COURIER';
    """)

    # CARGO_HAULER - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.9, average_threshold = 0.8, strong_threshold = 0.7
        WHERE ship_type = 'CARGO_HAULER';
    """)

    # DEFENDER - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.95, average_threshold = 0.9, strong_threshold = 0.8
        WHERE ship_type = 'DEFENDER';
    """)

    # COLONY_SHIP - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.97, average_threshold = 0.92, strong_threshold = 0.85
        WHERE ship_type = 'COLONY_SHIP';
    """)

    # CARRIER - Restore original harsh values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.99, average_threshold = 0.95, strong_threshold = 0.9
        WHERE ship_type = 'CARRIER';
    """)