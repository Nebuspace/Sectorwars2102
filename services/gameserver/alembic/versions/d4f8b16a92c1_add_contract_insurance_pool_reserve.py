"""Add contracts.insurance_pool_reserve (WO-CONTRACT-1b-CLAIM-SAFETY)

Purely additive: one new not-null Numeric(19,2) column on the existing
`contracts` table, default 0. Nothing existing is touched.

This is the REAL, persisted claims-fund balance a covered failure-event
offset draws from -- previously `insurance_pool_reserve` was only a
`post_player_contract()` function parameter folded into `escrow_amount`
at posting time (`escrow_amount = payment + insurance_pool_reserve`) and
never stored on its own; the split between "payment" and "pool" was lost
the instant escrow_amount was computed, leaving nothing for a claim to
draw against. See `contract_insurance.py`'s claim-offset engine and
`models/contract.py`'s own column docstring.

Backfill: every existing row gets 0 (server_default covers new inserts
automatically; this UPDATE covers rows that predate the column existing
at all). 0 is the conservative floor -- whatever pool amount an existing
player-posted contract's `escrow_amount` may have originally folded in is
unrecoverable from that column alone (payment and pool were never tracked
separately), so no free coverage is manufactured for a pre-existing row.
Going forward, `post_player_contract` persists the value here directly.

Revision ID: d4f8b16a92c1
Revises: f2adaac4162b
Create Date: 2026-07-17 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = 'd4f8b16a92c1'
down_revision = 'f2adaac4162b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'contracts',
        sa.Column(
            'insurance_pool_reserve', sa.Numeric(19, 2),
            nullable=False, server_default='0',
        ),
    )
    op.execute("UPDATE contracts SET insurance_pool_reserve = 0 WHERE insurance_pool_reserve IS NULL")


def downgrade() -> None:
    op.drop_column('contracts', 'insurance_pool_reserve')
