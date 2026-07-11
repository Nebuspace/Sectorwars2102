"""Add storage_lockers + contract_cargo_deposits tables (WO-STORE-LOCKER-MODEL)

Purely additive: two new tables, three new Postgres enum types. Nothing
existing is touched. Foundation of the storage kernel (S1) --
audit/design-briefs/heist-brief.html "01 / THE KERNEL" -- rent a locker
at a contract's destination Station, deliver in multi-trip installments,
flat rent accrues, the contract completes on full quantity.

Two of the three enum types (storage_locker_tier, storage_locker_risk_
state) are fully populated NOW for S2 forward-compat even though S1 only
ever writes their first member (basic / secure) -- see the model's own
module docstring for why this avoids a destructive migration later.

Schema-only: no business logic, no service wiring, this WO's entire scope.

Revision ID: 61b7e6f4ff93
Revises: cd5752f2b45d
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '61b7e6f4ff93'
down_revision = 'cd5752f2b45d'
branch_labels = None
depends_on = None


STATUS_VALUES = ('active', 'claimable', 'released')
TIER_VALUES = ('basic', 'reinforced', 'vault')
RISK_STATE_VALUES = ('secure', 'watched', 'targeted', 'breached')


def upgrade() -> None:
    op.create_table(
        'storage_lockers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'owner_player_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column(
            'station_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column(
            'contract_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('contracts.id', ondelete='SET NULL'), nullable=True,
        ),
        sa.Column(
            'status', sa.Enum(*STATUS_VALUES, name='storage_locker_status'),
            nullable=False, server_default='active',
        ),
        sa.Column(
            'tier', sa.Enum(*TIER_VALUES, name='storage_locker_tier'),
            nullable=False, server_default='basic',
        ),
        sa.Column(
            'risk_state', sa.Enum(*RISK_STATE_VALUES, name='storage_locker_risk_state'),
            nullable=False, server_default='secure',
        ),
        sa.Column('rent_rate', sa.Numeric(19, 2), nullable=False, server_default='1'),
        sa.Column('accrued_fee', sa.Numeric(19, 2), nullable=False, server_default='0'),
        sa.Column('last_fee_settled_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_storage_locker_owner', 'storage_lockers', ['owner_player_id'])
    op.create_index('idx_storage_locker_station', 'storage_lockers', ['station_id'])
    op.create_index('idx_storage_locker_contract', 'storage_lockers', ['contract_id'])

    op.create_table(
        'contract_cargo_deposits',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'locker_id', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('storage_lockers.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('commodity', sa.String(length=50), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column(
            'deposited_by', postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'), nullable=True,
        ),
        sa.Column('deposited_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_cargo_deposit_locker', 'contract_cargo_deposits', ['locker_id'])


def downgrade() -> None:
    op.drop_index('idx_cargo_deposit_locker', table_name='contract_cargo_deposits')
    op.drop_table('contract_cargo_deposits')

    op.drop_index('idx_storage_locker_contract', table_name='storage_lockers')
    op.drop_index('idx_storage_locker_station', table_name='storage_lockers')
    op.drop_index('idx_storage_locker_owner', table_name='storage_lockers')
    op.drop_table('storage_lockers')

    sa.Enum(name='storage_locker_risk_state').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='storage_locker_tier').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='storage_locker_status').drop(op.get_bind(), checkfirst=True)
