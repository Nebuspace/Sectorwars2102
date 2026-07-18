"""Ship registry schema (WO-P10-green-ship-registry-schema).

Canon: SYSTEMS/ship-registry.md (ADR-0008 S9 ship-registry overhaul),
DATA_MODELS/ships.md#shipregistry. Purely additive: 7 nullable columns on
``ships`` plus one new append-only table, ``ship_registry``, plus its
``registry_event_type`` enum. Nothing existing is touched, no NOT NULL
constraints against existing rows -- schema + auto-registration hook only,
no report/retract/transfer/salvage/board gameplay (Wave-2).

DOC CONFLICT NOTE: the dispatching WO's column sketch for ``ships`` omitted
``registered_owner_id``, but ship-registry.md's source map and
DATA_MODELS/ships.md:44 ("formerly owner_id") both name it as a required
Ship state addition -- added here (8th column, not 7) per "follow the DOC
on conflict." Nullable, matching the WO's "all ADDITIVE NULLABLE"
instruction for the rest of the set.

``registry_event_type`` values are canon's six ownership-affecting events
(ship-registry.md "Six ownership-affecting events") collapsed to
INITIAL_REGISTRATION / OWNERSHIP_TRANSFER (covers Trade, Abandon, and
Salvage's completed-transfer outcome -- they differ only in fee/dispute
metadata, not in what changed) / STOLEN_REPORTED / STOLEN_RETRACTED /
IMPOUNDED / ARCHIVED, matching DATA_MODELS/ships.md#shipregistry's
``event_type`` field exactly.

Revision ID: 4c7660b879f7
Revises: d4c8f6a12e93
Create Date: 2026-07-10 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '4c7660b879f7'
down_revision = 'd4c8f6a12e93'
branch_labels = None
depends_on = None


# Canon-exact lowercase values (DATA_MODELS/ships.md#shipregistry
# event_type field; src/models/ship_registry.py RegistryEventType).
REGISTRY_EVENT_VALUES = (
    'initial_registration',
    'ownership_transfer',
    'stolen_reported',
    'stolen_retracted',
    'impounded',
    'archived',
)


def upgrade() -> None:
    # --- ships: 8 additive nullable columns ---
    op.add_column('ships', sa.Column('registration_number', sa.String(length=15), nullable=True))
    op.add_column(
        'ships',
        sa.Column(
            'registered_owner_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=True,
        ),
    )
    op.add_column(
        'ships',
        sa.Column(
            'current_pilot_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column('ships', sa.Column('stolen_status', sa.Boolean(), nullable=True))
    op.add_column('ships', sa.Column('stolen_reported_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('ships', sa.Column('hatch_pin_code', sa.String(length=8), nullable=True))
    op.add_column('ships', sa.Column('for_sale_price', sa.Integer(), nullable=True))
    op.add_column(
        'ships',
        sa.Column(
            'for_sale_listed_by_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_unique_constraint('uq_ships_registration_number', 'ships', ['registration_number'])

    # --- ship_registry: append-only event log ---
    registry_event_enum = postgresql.ENUM(
        *REGISTRY_EVENT_VALUES, name='registry_event_type', create_type=False,
    )
    registry_event_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'ship_registry',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        # No ondelete action -- deliberately NOT CASCADE. The registry must
        # outlive the ship row (append-only audit-trail invariant); ships
        # are never hard-deleted in this codebase (status=DESTROYED is the
        # terminal state), so the RESTRICT-on-delete this leaves in place
        # structurally blocks ever hard-deleting a hull with history.
        sa.Column('ship_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ships.id'), nullable=False),
        sa.Column('registration_number', sa.String(length=15), nullable=False),
        sa.Column(
            'event_type',
            postgresql.ENUM(*REGISTRY_EVENT_VALUES, name='registry_event_type', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'original_owner_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'previous_owner_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'new_owner_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'acting_party_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('transfer_fee_paid', sa.Integer(), nullable=True),
        sa.Column(
            'port_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('stations.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Python attribute is "event_metadata" (Base.metadata collision) --
        # DB column stays named to match, see src/models/ship_registry.py.
        sa.Column('event_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
    )

    op.create_index('ix_ship_registry_ship_id', 'ship_registry', ['ship_id'])
    op.create_index('ix_ship_registry_registration_number', 'ship_registry', ['registration_number'])
    op.create_index('ix_ship_registry_original_owner_id', 'ship_registry', ['original_owner_id'])
    op.create_index('ix_ship_registry_ship_id_created_at', 'ship_registry', ['ship_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_ship_registry_ship_id_created_at', table_name='ship_registry')
    op.drop_index('ix_ship_registry_original_owner_id', table_name='ship_registry')
    op.drop_index('ix_ship_registry_registration_number', table_name='ship_registry')
    op.drop_index('ix_ship_registry_ship_id', table_name='ship_registry')
    op.drop_table('ship_registry')
    postgresql.ENUM(name='registry_event_type').drop(op.get_bind(), checkfirst=True)

    op.drop_constraint('uq_ships_registration_number', 'ships', type_='unique')
    op.drop_column('ships', 'for_sale_listed_by_id')
    op.drop_column('ships', 'for_sale_price')
    op.drop_column('ships', 'hatch_pin_code')
    op.drop_column('ships', 'stolen_reported_at')
    op.drop_column('ships', 'stolen_status')
    op.drop_column('ships', 'current_pilot_id')
    op.drop_column('ships', 'registered_owner_id')
    op.drop_column('ships', 'registration_number')
