"""add gate_construction_sites table (ADR-0078 staged construction)

WO-GWQ-GATE-STAGING -- a Warp Jumper's 200-unit hold cannot fit a phase's
1,500 (Phase 1) or 1,530 (Phase 3) unit material total in one trip, so each
phase's bulk ORE / EQUIPMENT / LUMEN_CRYSTALS accumulate in a
gate_construction_sites row (one per beacon+phase) across many partial
deposits before the phase commits (sw2102-docs FEATURES/galaxy/warp-gates.md
"Material staging", ADR-0078).

Additive only -- one new table + one new enum type
(gate_construction_site_status); no existing column altered or dropped.

Revision ID: a3f9e1c74b28
Revises: f4a8c1e6d930
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a3f9e1c74b28'
down_revision = 'f4a8c1e6d930'
branch_labels = None
depends_on = None


SITE_STATUS_VALUES = ('STAGING', 'CURING', 'READY', 'CONSUMED', 'CANCELLED')


def upgrade() -> None:
    op.create_table(
        'gate_construction_sites',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'beacon_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('warp_gate_beacons.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'gate_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('warp_gates.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('phase', sa.Integer(), nullable=False),
        sa.Column('required_ore', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('required_equipment', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('required_lumen', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('staged_ore', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('staged_equipment', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('staged_lumen', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('turns_applied', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('cure_completes_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'status',
            sa.Enum(*SITE_STATUS_VALUES, name='gate_construction_site_status'),
            nullable=False,
            server_default='STAGING',
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        'ix_gate_construction_sites_beacon_phase',
        'gate_construction_sites',
        ['beacon_id', 'phase'],
    )


def downgrade() -> None:
    op.drop_index('ix_gate_construction_sites_beacon_phase', table_name='gate_construction_sites')
    op.drop_table('gate_construction_sites')
    sa.Enum(name='gate_construction_site_status').drop(op.get_bind(), checkfirst=True)
