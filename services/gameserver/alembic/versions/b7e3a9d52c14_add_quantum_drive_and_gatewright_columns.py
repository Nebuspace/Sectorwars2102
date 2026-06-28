"""add quantum drive + gatewright columns (players, ships) and HARMONIZING status

Quantum Drive / Warp Jumper slice (sw2102-docs ADR-0030 multi-step commit,
ADR-0009 venue split, ADR-0011/0029/0036 warp gate construction):

  - ``players.quantum_shards`` / ``players.quantum_crystals`` INT NOT NULL
    DEFAULT 0 — the player's quantum resource wallet.

  - ``ships.quantum_charges`` INT NOT NULL DEFAULT 0 — refined charges in
    the Warp Jumper's special-equipment slot (ADR-0030: NOT regular cargo).

  - ``ships.quantum_jump_cooldown_until`` / ``ships.quantum_scan_cooldown_until``
    TIMESTAMPTZ NULL — wall-clock cooldown deadlines, pre-scaled through
    src.core.game_time.scaled_deadline at set time (24h jump / 4h scan).

  - ``ships.harmonization_completes_at`` TIMESTAMPTZ NULL — set while a WJ
    is anchored to a beacon and harmonizing into a warp gate (ADR-0036).

  - ``ships.destruction_cause`` VARCHAR NULL — e.g. 'WARP_GATE_ANCHOR'
    (ADR-0029: the WJ is consumed as the gate's anchor mass) or 'combat'.

  - ``ship_status`` enum gains ``HARMONIZING``.

ALTER TYPE ... ADD VALUE runs inside ``op.get_context().autocommit_block()``
(the d4f7a2c91e58 precedent): alembic commits the in-progress migration
transaction, executes the block with AUTOCOMMIT isolation, then begins a
fresh transaction. A direct mid-transaction isolation switch raises
InvalidRequestError on SQLAlchemy 2.x. All plain column DDL runs FIRST,
inside the normal transaction; the only non-transactional work is the one
enum append. Idempotent via IF NOT EXISTS.

Revision ID: b7e3a9d52c14
Revises: d4f7a2c91e58
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e3a9d52c14'
down_revision = 'd4f7a2c91e58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- players: quantum resource wallet ---
    op.add_column(
        'players',
        sa.Column('quantum_shards', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'players',
        sa.Column('quantum_crystals', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )

    # --- ships: quantum drive + gate construction columns ---
    op.add_column(
        'ships',
        sa.Column('quantum_charges', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    op.add_column(
        'ships',
        sa.Column('quantum_jump_cooldown_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'ships',
        sa.Column('quantum_scan_cooldown_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'ships',
        sa.Column('harmonization_completes_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'ships',
        sa.Column('destruction_cause', sa.String(), nullable=True),
    )

    # --- ship_status enum: add HARMONIZING ---
    # autocommit_block() per the module docstring / d4f7a2c91e58 precedent.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE ship_status ADD VALUE IF NOT EXISTS 'HARMONIZING'")
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE: HARMONIZING remains on the
    # ship_status enum after downgrade (same residue as the d4f7a2c91e58 /
    # c4f1d8b27e63 enum precedents). Park any harmonizing hulls back in
    # space so no row references the now-unmodeled value.
    op.execute("UPDATE ships SET status = 'IN_SPACE' WHERE status = 'HARMONIZING'")

    op.drop_column('ships', 'destruction_cause')
    op.drop_column('ships', 'harmonization_completes_at')
    op.drop_column('ships', 'quantum_scan_cooldown_until')
    op.drop_column('ships', 'quantum_jump_cooldown_until')
    op.drop_column('ships', 'quantum_charges')
    op.drop_column('players', 'quantum_crystals')
    op.drop_column('players', 'quantum_shards')
