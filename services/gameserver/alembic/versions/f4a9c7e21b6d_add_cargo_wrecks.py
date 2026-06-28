"""add cargo_wrecks table (salvageable debris)

Canon: DATA_MODELS/cargo-wrecks.md (+ ADR-0007 grace/Suspect,
ADR-0055 S-F2 killing-blow attribution).

Additive only — one new table + one new enum type (``wreck_cause``).
The ``ship_type`` PG enum already exists (created with the ``ships``
table), so the ``destroyed_ship_type`` column REUSES it with
``create_type=False``; only ``wreck_cause`` is created here.

Lifecycle is row-delete-on-empty (no decay timer): rows are inserted on
ship destruction and DELETED by the salvage service the moment ``cargo``
becomes ``{}``. No periodic cleanup job is required.

Revision ID: f4a9c7e21b6d
Revises: a1d7c4e92f6b
Create Date: 2026-06-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f4a9c7e21b6d'
down_revision = 'a1d7c4e92f6b'
branch_labels = None
depends_on = None


# New enum type for this table. SELF_DESTRUCT is reserved (never actually
# spawns a wreck); WARP_GATE_ANCHOR is intentionally absent (anchor
# destructions spawn no wreck).
WRECK_CAUSE_VALUES = ('COMBAT', 'HAZARD', 'SELF_DESTRUCT', 'ABANDONMENT_EXPIRED')

# The destroyed-hull type reuses the EXISTING ``ship_type`` PG enum created
# with the ships table — declared here with create_type=False so create_table
# does not try to (re-)create it.
ship_type_enum = postgresql.ENUM(name='ship_type', create_type=False)


def upgrade() -> None:
    op.create_table(
        'cargo_wrecks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'sector_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('sectors.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'original_owner_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'original_team_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('teams.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'killing_blow_pilot_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'destroyed_ship_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('ships.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('destroyed_ship_type', ship_type_enum, nullable=False),
        sa.Column('cargo', postgresql.JSONB(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'cause',
            sa.Enum(*WRECK_CAUSE_VALUES, name='wreck_cause'),
            nullable=False,
        ),
    )
    op.create_index('ix_cargo_wrecks_sector', 'cargo_wrecks', ['sector_id'])
    op.create_index(
        'ix_cargo_wrecks_owner_created',
        'cargo_wrecks',
        ['original_owner_id', 'created_at'],
    )
    op.create_index(
        'ix_cargo_wrecks_killer_created',
        'cargo_wrecks',
        ['killing_blow_pilot_id', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_cargo_wrecks_killer_created', table_name='cargo_wrecks')
    op.drop_index('ix_cargo_wrecks_owner_created', table_name='cargo_wrecks')
    op.drop_index('ix_cargo_wrecks_sector', table_name='cargo_wrecks')
    op.drop_table('cargo_wrecks')
    # Drop only the enum WE created; the shared ship_type enum is left intact.
    sa.Enum(name='wreck_cause').drop(op.get_bind(), checkfirst=True)
