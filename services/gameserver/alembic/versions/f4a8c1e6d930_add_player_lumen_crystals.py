"""add player lumen crystal ledger + refine job slot

Lumen Crystal supply-chain slice (sw2102-docs quantum-resources.md:223-237,
ADR-0037 — WO-GWQ-LUMEN-FAUCET):

  - ``players.lumen_crystals`` INT NOT NULL DEFAULT 0 — the player's Lumen
    Crystal wallet. Credited by quantum_service.harvest_nebula's
    Emerald/Crimson drop roll and by refining_service.collect_lumen_refine.

  - ``players.lumen_refine_ready_at`` TIMESTAMPTZ NULL — wall-clock deadline
    for a single in-flight Class-5+ Shard-to-Crystal (Lumen) refine job,
    pre-scaled through src.core.game_time.scaled_deadline at start time
    (12h canonical). NULL means no job is pending.

Both columns are additive-only; no existing column is altered or dropped.

Revision ID: f4a8c1e6d930
Revises: cdea102d345b
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4a8c1e6d930'
down_revision = 'cdea102d345b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'players',
        sa.Column('lumen_crystals', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'players',
        sa.Column('lumen_refine_ready_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('players', 'lumen_refine_ready_at')
    op.drop_column('players', 'lumen_crystals')
