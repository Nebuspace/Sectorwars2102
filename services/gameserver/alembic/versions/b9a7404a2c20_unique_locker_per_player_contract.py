"""Add unique (owner_player_id, contract_id) index on storage_lockers (WO-STORE-DEPOSIT-FLOW)

Purely additive: one new index on an existing table, no column/table
changes. Guards against two concurrent get_or_create_locker calls
minting duplicate lockers for the same (player, contract) pair --
storage_service.get_or_create_locker already locks the Player row before
its own check-then-insert (closing the same-player race at the
application level), this index is the belt-and-suspenders DB-level
guarantee for any future call path that might bypass that lock.

Postgres unique indexes never treat two NULLs as equal, so multiple
lockers with contract_id=NULL (standalone CLAIMABLE storage, no longer
tied to a contract) for the same owner remain unrestricted -- exactly
the semantics wanted (canon: a player can hold several unrelated
claimable lockers, but never two active lockers for the SAME contract).

Revision ID: b9a7404a2c20
Revises: 61b7e6f4ff93
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b9a7404a2c20'
down_revision = '61b7e6f4ff93'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'idx_storage_locker_owner_contract_unique', 'storage_lockers',
        ['owner_player_id', 'contract_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_index('idx_storage_locker_owner_contract_unique', table_name='storage_lockers')
