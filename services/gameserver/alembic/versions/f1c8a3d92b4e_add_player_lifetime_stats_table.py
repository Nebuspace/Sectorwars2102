"""Add player_lifetime_stats table (WO-BUILD-RANKING-LIFETIME-STATS-TABLE).

Persists the achievement counters ranking_service.get_player_stats()
otherwise recomputes ad hoc from MarketTransaction/CombatLog/
ARIAExplorationMap/Planet on every call (FEATURES/gameplay/ranking.md
'lifetime-stats table' gap). One row per player, updated incrementally at
the existing rank-point award call sites; backfilled once from current
per-player counts for any player missing a row.

Fully additive — new table only, no existing column changes.

Revision ID: f1c8a3d92b4e
Revises: d5379e3fe37b
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f1c8a3d92b4e"
down_revision = "d5379e3fe37b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_lifetime_stats",
        sa.Column("player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("combat_victories", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sectors_visited", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planets_owned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id"),
    )

    # One-time backfill from existing data so pre-existing players start
    # with an accurate row instead of all-zero counters.
    op.execute(
        """
        INSERT INTO player_lifetime_stats
            (player_id, total_trades, combat_victories, sectors_visited, planets_owned, updated_at)
        SELECT
            p.id,
            COALESCE((SELECT COUNT(*) FROM enhanced_market_transactions mt WHERE mt.player_id = p.id), 0),
            COALESCE((
                SELECT COUNT(*) FROM combat_logs cl
                WHERE (cl.attacker_id = p.id AND cl.outcome = 'attacker_win' AND cl.defender_id IS NOT NULL)
                   OR (cl.defender_id = p.id AND cl.outcome = 'defender_win')
            ), 0),
            COALESCE((SELECT COUNT(*) FROM aria_exploration_maps em WHERE em.player_id = p.id), 0),
            COALESCE((SELECT COUNT(*) FROM planets pl WHERE pl.owner_id = p.id), 0),
            now()
        FROM players p
        ON CONFLICT (player_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("player_lifetime_stats")
