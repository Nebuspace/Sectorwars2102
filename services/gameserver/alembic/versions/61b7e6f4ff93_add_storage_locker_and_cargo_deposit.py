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
    # Explicit checkfirst=True creates (WO-QTI-STORAGE-LOCKER-DUPTYPE-FIX):
    # a genuinely-fresh CI chain reproducibly hits `psycopg2.errors.
    # DuplicateObject: type "storage_locker_status" already exists` right
    # here (confirmed via a live schema-parity run, e.g. GH Actions run
    # 29649755837) even though this is the ONLY place in the entire
    # migration history that names these three enums -- grepped the full
    # alembic/versions/ tree and every model file, zero other hits. The
    # exact upstream mechanism creating the type before this line runs
    # was NOT pinned down (single linear alembic head confirmed, no other
    # create_all()/CREATE TYPE site found, full CI log shows zero earlier
    # mention of storage_locker anywhere) -- but checkfirst=True/
    # create_type=False makes this migration idempotent regardless of
    # cause, matching the exact precedent already used for this same
    # failure class elsewhere in this history (a2f6d9b41c83 ship_size,
    # d4c8f6a12e93 multi-account severity, 9f1e216e2321 phantom-table
    # catch-up, f8d3a1c9e527 NPC drift repair -- all checkfirst=True).
    bind = op.get_bind()
    status_type = postgresql.ENUM(*STATUS_VALUES, name='storage_locker_status')
    tier_type = postgresql.ENUM(*TIER_VALUES, name='storage_locker_tier')
    risk_state_type = postgresql.ENUM(*RISK_STATE_VALUES, name='storage_locker_risk_state')
    status_type.create(bind, checkfirst=True)
    tier_type.create(bind, checkfirst=True)
    risk_state_type.create(bind, checkfirst=True)

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
            # create_type=False: the type is created explicitly above:
            # op.create_table must not attempt to CREATE TYPE a second
            # time (the exact class of bug this migration hit).
            'status', postgresql.ENUM(*STATUS_VALUES, name='storage_locker_status', create_type=False),
            nullable=False, server_default='active',
        ),
        sa.Column(
            'tier', postgresql.ENUM(*TIER_VALUES, name='storage_locker_tier', create_type=False),
            nullable=False, server_default='basic',
        ),
        sa.Column(
            'risk_state', postgresql.ENUM(*RISK_STATE_VALUES, name='storage_locker_risk_state', create_type=False),
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
