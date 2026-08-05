"""reputations (player_id, faction_id) UNIQUE + leaderboard indexes (SK32)

DATA_MODELS/player.md § Reputation claims these as shipped; the ORM and
the initial_schema create_table never added them. Additive-only:

  - uq_reputations_player_faction UNIQUE (player_id, faction_id)
  - ix_reputations_faction_current_value (faction_id, current_value DESC)
  - ix_reputations_player_id (player_id)

*** APPLY PRECONDITION — READ BEFORE RUNNING ***
The UNIQUE constraint fails loudly (UniqueViolation) if any duplicate
(player_id, faction_id) pairs exist. Confirm first:

    SELECT player_id, faction_id, COUNT(*)
    FROM reputations
    GROUP BY player_id, faction_id
    HAVING COUNT(*) > 1;

If rows return, dedupe under a deploy window BEFORE upgrade (keep the
most recently ``last_updated`` row per pair). Do not silently delete
inside this migration.

Revision ID: c2f8a1e94b70
Revises: b8e2c4a91f70
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa


revision = "c2f8a1e94b70"
down_revision = "b8e2c4a91f70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_reputations_player_faction",
        "reputations",
        ["player_id", "faction_id"],
    )
    op.create_index(
        "ix_reputations_faction_current_value",
        "reputations",
        ["faction_id", sa.text("current_value DESC")],
    )
    op.create_index(
        "ix_reputations_player_id",
        "reputations",
        ["player_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reputations_player_id", table_name="reputations")
    op.drop_index(
        "ix_reputations_faction_current_value", table_name="reputations"
    )
    op.drop_constraint(
        "uq_reputations_player_faction", "reputations", type_="unique"
    )
