"""Add aria_trust_score / aria_violation_count / aria_blocked_until to players (WO-ARIA-TRUST-PERSIST)

Purely additive: three new columns on an existing table, nothing else
touched.

Canon: OPERATIONS/aria.md:239-241, per ADR-0065 (Group K schema/data-model
parity) M-U1. These columns were previously orphan -- no FEATURES/SYSTEMS/
OPERATIONS reference -- and this migration is what finally backs the
in-memory-only AISecurityService violation ladder with real persistence
(see services/ai_security_service.py's write-through helpers). Defaults
match the ladder's own starting state exactly (trust=1.0, violations=0,
never blocked) so an existing player row reads identically to a
never-touched in-memory profile on first seed.

Revision ID: 4299dadf325b
Revises: 641c08f78f35
Create Date: 2026-07-10 04:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4299dadf325b'
down_revision = '641c08f78f35'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'players',
        sa.Column('aria_trust_score', sa.Float(), nullable=False, server_default=sa.text('1.0')),
    )
    op.add_column(
        'players',
        sa.Column('aria_violation_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'players',
        sa.Column('aria_blocked_until', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('players', 'aria_blocked_until')
    op.drop_column('players', 'aria_violation_count')
    op.drop_column('players', 'aria_trust_score')
