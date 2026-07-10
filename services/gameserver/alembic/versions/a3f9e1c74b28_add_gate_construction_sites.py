"""add gate_construction_sites table (ADR-0078 staged construction)

WO-GWQ-GATE-STAGING -- a Warp Jumper's 200-unit hold cannot fit a phase's
1,500 (Phase 1) or 1,530 (Phase 3) unit material total in one trip, so each
phase's bulk ORE / EQUIPMENT / LUMEN_CRYSTALS accumulate in a
gate_construction_sites row (one per beacon+phase) across many partial
deposits before the phase commits (sw2102-docs FEATURES/galaxy/warp-gates.md
"Material staging", ADR-0078).

Additive only -- one new table + one new enum type
(gate_construction_site_status); no existing column altered or dropped.

FIXED, WO-QTI-MIGRATION-CHAIN-FRESH phantom-table audit round 3: this
revision's gate_construction_sites.beacon_id / .gate_id columns carry inline
``sa.ForeignKey('warp_gate_beacons.id', ...)`` / ``sa.ForeignKey('warp_gates.id',
...)`` references, but NEITHER warp_gate_beacons NOR warp_gates is ever
created by any migration in this history (grepped the full versions/ tree --
this is the ONLY file that even mentions either name) -- both are
src/models/warp_gate.py tables that, like construction_reservations
(e7c4a1b9d602), were only ever materialized via Base.metadata.create_all.
This is the SAME phantom-table class the audit's round-1 pass fixed for
construction_reservations, missed here because the reference is embedded
inside a column's inline ForeignKey rather than a bare op.add_column/
alter_column/execute call -- the scanner's round-3 fix now extracts embedded
sa.ForeignKey(...) / sa.ForeignKeyConstraint(...) targets too. Added guarded
catch-up creation (both tables + their two enum types, warp_gate_beacons
before warp_gates per their own FK dependency) immediately before the
existing gate_construction_sites create_table, mirroring the f8d3a1c9e527 /
e7c4a1b9d602 precedent. warp_tunnels/players/ships (this revision's other FK
targets) are all core tables created in c138b33baec4 -- verified, not
phantom.

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

# Phantom-table catch-up enum values (src/models/warp_gate.py).
WARP_GATE_BEACON_STATUS_VALUES = ('DEPLOYED', 'MATCHED', 'EXPIRED', 'CANCELLED')
WARP_GATE_STATUS_VALUES = ('HARMONIZING', 'ACTIVE', 'CANCELLED', 'COLLAPSED')


def upgrade() -> None:
    # Phantom-table catch-up (see module docstring) -- src/models/warp_gate.py's
    # WarpGateBeacon then WarpGate tables (dependency order: WarpGate.beacon_id
    # references warp_gate_beacons), plus their two enum types. Idempotent via
    # sa.Enum(...).create(checkfirst=True) (mirrors f8d3a1c9e527) and
    # CREATE TABLE IF NOT EXISTS (mirrors f8d3a1c9e527 / e7c4a1b9d602).
    sa.Enum(*WARP_GATE_BEACON_STATUS_VALUES, name='warp_gate_beacon_status').create(
        op.get_bind(), checkfirst=True
    )
    sa.Enum(*WARP_GATE_STATUS_VALUES, name='warp_gate_status').create(
        op.get_bind(), checkfirst=True
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS warp_gate_beacons (
            id UUID PRIMARY KEY,
            player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            source_sector_id INTEGER NOT NULL,
            destination_sector_id INTEGER NOT NULL,
            status warp_gate_beacon_status NOT NULL DEFAULT 'DEPLOYED',
            invulnerable_until TIMESTAMPTZ,
            hp INTEGER NOT NULL DEFAULT 5000,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS warp_gates (
            id UUID PRIMARY KEY,
            beacon_id UUID NOT NULL REFERENCES warp_gate_beacons(id) ON DELETE CASCADE,
            player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            warp_tunnel_id UUID REFERENCES warp_tunnels(id) ON DELETE SET NULL,
            status warp_gate_status NOT NULL DEFAULT 'HARMONIZING',
            hp INTEGER NOT NULL DEFAULT 5000,
            harmonization_completes_at TIMESTAMPTZ,
            anchor_ship_id UUID REFERENCES ships(id) ON DELETE SET NULL,
            construction_cost INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

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
    # The phantom-table catch-up (warp_gate_beacons, warp_gates, and their two
    # enum types) added to upgrade() is deliberately NOT reversed here -- same
    # reasoning as e7c4a1b9d602's downgrade(): the common case is a DB where
    # both tables pre-existed this migration (create_all-born, holding real
    # beacon/gate rows), and dropping them on downgrade would destroy data
    # this migration never owned creating.
    op.drop_index('ix_gate_construction_sites_beacon_phase', table_name='gate_construction_sites')
    op.drop_table('gate_construction_sites')
    sa.Enum(name='gate_construction_site_status').drop(op.get_bind(), checkfirst=True)
