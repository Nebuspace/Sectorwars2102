"""Add contracts table (WO-ECON-CONTRACT-1-KERNEL)

Purely additive: one new table, six new Postgres enum types. Nothing
existing is touched.

Canon: FEATURES/economy/contracts.md:25-63 (full schema, built as one
additive whole). Only the `posted -> accepted -> completed` / `abandon` /
`expire` transitions on `cargo_delivery` are exercised this WO
(contract_service.py + contract_generator.py) -- bulk-procurement partial
fulfillment, player-issued posting/escrow, insurance, and disputes are
later build steps (contracts.md:421-431, steps 4/6/7) and read/write none
of their columns yet; those columns exist now so the table never needs a
second schema migration when those steps land.

Revision ID: 1aab831e9008
Revises: b3e1b652afc8
Create Date: 2026-07-10 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '1aab831e9008'
down_revision = 'b3e1b652afc8'
branch_labels = None
depends_on = None


ISSUER_TYPE_VALUES = ('npc', 'player')
CONTRACT_TYPE_VALUES = (
    'cargo_delivery', 'bulk_procurement', 'express_delivery',
    'hazardous_transport', 'refugee_transport', 'acquisition_bounty', 'escort',
)
STATUS_VALUES = (
    'posted', 'accepted', 'in_progress', 'partial_fulfilled',
    'completed', 'cancelled', 'disputed', 'expired',
)
ESCROW_STATE_VALUES = ('held', 'released', 'disputed', 'refunding')
DISPUTE_RESOLUTION_VALUES = ('full_payout', 'partial_payout', 'refund', 'split', 'penalty')
INSURANCE_TIER_VALUES = ('basic', 'standard', 'hazard')


def upgrade() -> None:
    op.create_table(
        'contracts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'issuer_type',
            sa.Enum(*ISSUER_TYPE_VALUES, name='contract_issuer_type'),
            nullable=False,
        ),
        sa.Column('issuer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'acceptor_player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'contract_type',
            sa.Enum(*CONTRACT_TYPE_VALUES, name='contract_type'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(*STATUS_VALUES, name='contract_status'),
            nullable=False,
            server_default='posted',
        ),
        sa.Column(
            'origin_station_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'destination_station_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('commodity_type', sa.String(length=50), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=True),
        sa.Column('payment', sa.Numeric(19, 2), nullable=False),
        sa.Column('penalty', sa.Numeric(19, 2), nullable=False),
        sa.Column('acceptance_fee_pct', sa.Numeric(5, 2), nullable=False, server_default='2.0'),
        sa.Column('escrow_amount', sa.Numeric(19, 2), nullable=False, server_default='0'),
        sa.Column(
            'escrow_state',
            sa.Enum(*ESCROW_STATE_VALUES, name='contract_escrow_state'),
            nullable=False,
            server_default='held',
        ),
        sa.Column(
            'faction_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('factions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('reputation_reward', sa.Integer(), nullable=True),
        sa.Column('reputation_penalty', sa.Integer(), nullable=True),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=False),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('partial_fulfilled_amount', sa.Integer(), nullable=True),
        sa.Column('partial_fulfilled_payout', sa.Numeric(19, 2), nullable=False, server_default='0'),
        sa.Column('dispute_filed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'dispute_resolution',
            sa.Enum(*DISPUTE_RESOLUTION_VALUES, name='contract_dispute_resolution'),
            nullable=True,
        ),
        sa.Column('dispute_resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dispute_notes', sa.Text(), nullable=True),
        sa.Column('escalated_to_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column(
            'insurance_coverage_tier',
            sa.Enum(*INSURANCE_TIER_VALUES, name='contract_insurance_coverage_tier'),
            nullable=True,
        ),
        sa.Column('insurance_premium_paid', sa.Numeric(19, 2), nullable=False, server_default='0'),
        sa.Column('insurance_claim_filed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column(
            'posting_stations',
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default='{}',
        ),
    )

    op.create_index(
        'idx_contract_board_listing', 'contracts',
        ['status', 'destination_station_id', 'posted_at'],
    )
    op.create_index('idx_contract_issuer_status', 'contracts', ['issuer_id', 'status'])
    op.create_index('idx_contract_acceptor_status', 'contracts', ['acceptor_player_id', 'status'])
    op.create_index('idx_contract_deadline', 'contracts', ['deadline'])
    op.create_index('idx_contract_dispute_queue', 'contracts', ['status', 'dispute_filed_at'])


def downgrade() -> None:
    op.drop_index('idx_contract_dispute_queue', table_name='contracts')
    op.drop_index('idx_contract_deadline', table_name='contracts')
    op.drop_index('idx_contract_acceptor_status', table_name='contracts')
    op.drop_index('idx_contract_issuer_status', table_name='contracts')
    op.drop_index('idx_contract_board_listing', table_name='contracts')
    op.drop_table('contracts')
    sa.Enum(name='contract_insurance_coverage_tier').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='contract_dispute_resolution').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='contract_escrow_state').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='contract_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='contract_type').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='contract_issuer_type').drop(op.get_bind(), checkfirst=True)
