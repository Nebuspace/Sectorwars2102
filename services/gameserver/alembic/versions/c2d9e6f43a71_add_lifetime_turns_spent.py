"""add players.lifetime_turns_spent

Living NPC System Phase 2 prerequisite (ADR-0042): police arrival
watchers trigger at ``offense_turn + 2`` on a CUMULATIVE turn clock.
``players.turns`` is a regenerating balance, so a monotonic counter is
added; every spend site now routes through turn_service.spend_turns /
refund_turns (refunds decrement — a refunded action never happened for
arrival-watcher purposes).

Existing players start at 0 — the watcher math only ever compares
deltas against the clock, so the starting offset is irrelevant.

Revision ID: c2d9e6f43a71
Revises: a9c4e7f21d83
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d9e6f43a71'
down_revision = 'a9c4e7f21d83'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'players',
        sa.Column(
            'lifetime_turns_spent',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    op.drop_column('players', 'lifetime_turns_spent')
