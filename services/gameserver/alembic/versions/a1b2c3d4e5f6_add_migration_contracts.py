"""migration_contracts table — pioneer migration contract layer

Population-center Pioneer Office (FEATURES/planets/colonization.md): a
tracked cohort brokered at a capital population hub. ``loaded`` mirrors
pioneers riding in cargo against the contract; ``delivered`` advances as
they settle on frontier worlds (claim/disembark ledger in
pioneer_service). Indexed on (player_id, status) for the active-contract
list and the settlement match, and on (player_id, source_planet_id) for
the load-batch lookup.

Revision ID: a1b2c3d4e5f6
Revises: f8d3a1c9e527
Create Date: 2026-06-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f8d3a1c9e527'
branch_labels = None
depends_on = None


MIGRATION_CONTRACT_STATUS_VALUES = ('BROKERED', 'IN_PROGRESS', 'FULFILLED', 'VOID')


def upgrade() -> None:
    op.create_table(
        'migration_contracts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'source_planet_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('planets.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('source_sector_id', sa.Integer(), nullable=False),
        sa.Column('cohort_total', sa.Integer(), nullable=False),
        sa.Column('loaded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('delivered', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fee_per_pioneer_locked', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(*MIGRATION_CONTRACT_STATUS_VALUES, name='migration_contract_status'),
            nullable=False,
            server_default='BROKERED',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        'ix_migration_contracts_player_status',
        'migration_contracts',
        ['player_id', 'status'],
    )
    op.create_index(
        'ix_migration_contracts_player_source',
        'migration_contracts',
        ['player_id', 'source_planet_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_migration_contracts_player_source', table_name='migration_contracts')
    op.drop_index('ix_migration_contracts_player_status', table_name='migration_contracts')
    op.drop_table('migration_contracts')
    sa.Enum(name='migration_contract_status').drop(op.get_bind(), checkfirst=True)
