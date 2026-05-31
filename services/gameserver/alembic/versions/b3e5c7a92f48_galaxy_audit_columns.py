"""galaxy_audit_columns for bang integration

Adds the import_state lifecycle gate + bang provenance columns to the
galaxies table. See DOCS/PLANS/bang-integration.md § Phase 1B.

Revision ID: b3e5c7a92f48
Revises: a8d1f4e6c920
Create Date: 2026-05-31 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b3e5c7a92f48'
down_revision = 'a8d1f4e6c920'
branch_labels = None
depends_on = None


GALAXY_IMPORT_STATE_VALUES = (
    'GENERATING',
    'READY',
    'FAILED',
)


def upgrade() -> None:
    # Create the enum type up-front so server_default='READY' is valid.
    galaxy_import_state = sa.Enum(
        *GALAXY_IMPORT_STATE_VALUES,
        name='galaxy_import_state',
    )
    galaxy_import_state.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'galaxies',
        sa.Column(
            'import_state',
            galaxy_import_state,
            nullable=False,
            server_default='READY',
        ),
    )
    op.add_column(
        'galaxies',
        sa.Column('bang_version', sa.String(length=20), nullable=True),
    )
    op.add_column(
        'galaxies',
        sa.Column('bang_seed', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'galaxies',
        sa.Column('bang_config_hash', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'galaxies',
        sa.Column('bang_snapshot', postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        'galaxies',
        sa.Column(
            'generation_warnings',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column('galaxies', 'generation_warnings')
    op.drop_column('galaxies', 'bang_snapshot')
    op.drop_column('galaxies', 'bang_config_hash')
    op.drop_column('galaxies', 'bang_seed')
    op.drop_column('galaxies', 'bang_version')
    op.drop_column('galaxies', 'import_state')
    sa.Enum(name='galaxy_import_state').drop(op.get_bind(), checkfirst=True)
