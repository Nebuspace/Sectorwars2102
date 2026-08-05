"""tighten_scout_ship_thresholds_for_strict_consistency

Revision ID: 6b1d95a38c98
Revises: ec92f8afd44a
Create Date: 2025-11-18 00:22:44.966874

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '6b1d95a38c98'
down_revision = 'ec92f8afd44a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Tighten ship persuasion thresholds to account for new consistency-weighted scoring (50%).

    With consistency now at 50% weight (up from 20%), the scoring formula has changed from:
      OLD: (persuasiveness * 0.5) + (confidence * 0.3) + (consistency * 0.2)
      NEW: (consistency * 0.5) + (confidence * 0.3) + (persuasiveness * 0.2)

    This makes lying/contradictions MUCH more punishing, so we need tighter thresholds
    to maintain game balance and ensure interrogation difficulty is realistic.

    Samantha's Recommendation: Scout Ship is a valuable asset worth 500k+ credits.
    Guards should be VERY skeptical of someone claiming ownership without perfect consistency.
    """

    # SCOUT_SHIP - Now MUCH harder (consistency is king)
    # Weak negotiators need near-perfect consistency (0.7)
    # Average negotiators still need very high consistency (0.6)
    # Strong negotiators can get away with minor inconsistencies (0.5)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.7, average_threshold = 0.6, strong_threshold = 0.5
        WHERE ship_type = 'SCOUT_SHIP';
    """)

    # LIGHT_FREIGHTER - Also tightened (tier 2 ship)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.55, average_threshold = 0.5, strong_threshold = 0.45
        WHERE ship_type = 'LIGHT_FREIGHTER';
    """)

    # FAST_COURIER - Even stricter (tier 3, fast ship)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.75, average_threshold = 0.65, strong_threshold = 0.55
        WHERE ship_type = 'FAST_COURIER';
    """)

    # CARGO_HAULER - Very strict (tier 4, high value)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.8, average_threshold = 0.7, strong_threshold = 0.6
        WHERE ship_type = 'CARGO_HAULER';
    """)

    # DEFENDER - Extremely strict (tier 5, combat ship)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.85, average_threshold = 0.75, strong_threshold = 0.65
        WHERE ship_type = 'DEFENDER';
    """)

    # COLONY_SHIP - Nearly impossible (tier 6)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.9, average_threshold = 0.8, strong_threshold = 0.7
        WHERE ship_type = 'COLONY_SHIP';
    """)

    # CARRIER - Almost requires perfection (tier 7)
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.95, average_threshold = 0.85, strong_threshold = 0.75
        WHERE ship_type = 'CARRIER';
    """)


def downgrade() -> None:
    """
    Restore previous balanced thresholds (before consistency re-weighting).
    """

    # SCOUT_SHIP - Restore pre-consistency-fix values
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.5, average_threshold = 0.45, strong_threshold = 0.4
        WHERE ship_type = 'SCOUT_SHIP';
    """)

    # LIGHT_FREIGHTER - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.4, average_threshold = 0.35, strong_threshold = 0.3
        WHERE ship_type = 'LIGHT_FREIGHTER';
    """)

    # FAST_COURIER - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.55, average_threshold = 0.5, strong_threshold = 0.45
        WHERE ship_type = 'FAST_COURIER';
    """)

    # CARGO_HAULER - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.65, average_threshold = 0.55, strong_threshold = 0.5
        WHERE ship_type = 'CARGO_HAULER';
    """)

    # DEFENDER - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.75, average_threshold = 0.65, strong_threshold = 0.6
        WHERE ship_type = 'DEFENDER';
    """)

    # COLONY_SHIP - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.8, average_threshold = 0.7, strong_threshold = 0.65
        WHERE ship_type = 'COLONY_SHIP';
    """)

    # CARRIER - Restore
    op.execute("""
        UPDATE ship_rarity_configs
        SET weak_threshold = 0.85, average_threshold = 0.75, strong_threshold = 0.7
        WHERE ship_type = 'CARRIER';
    """)
