"""ARIA data index registry (WO-P6-aria-data-index-registry).

Canon: DATA_MODELS/aria-data-index.md (ADR-0092). Purely additive: one new
table, `aria_data_streams`, plus its two enum types (`aria_data_stream_
domain`, `aria_data_stream_retention`). Nothing existing is touched -- the
memory_type refactor this WO's Lane C was scoped for did NOT land (see the
WO report: none of the three memory_type literals actually written today
by aria_personal_intelligence_service.py byte-match a registry key here,
per the WO's own "STOP and report the mismatch, don't invent a mapping"
instruction), so aria_personal_memories.memory_type is untouched by this
migration.

Revision ID: 26ea004450dc
Revises: 4c7660b879f7
Create Date: 2026-07-10 17:06:07.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '26ea004450dc'
down_revision = '4c7660b879f7'
branch_labels = None
depends_on = None


# Canon-exact lowercase values (DATA_MODELS/aria-data-index.md; src/models/
# aria_data_stream.py ARIADataStreamDomain / ARIADataStreamRetention).
DOMAIN_VALUES = ('nav', 'commerce', 'threat', 'asset', 'social', 'meta')
RETENTION_VALUES = ('permanent', 'rolling_90d', 'budget_pruned')


def upgrade() -> None:
    domain_enum = postgresql.ENUM(
        *DOMAIN_VALUES, name='aria_data_stream_domain', create_type=False,
    )
    domain_enum.create(op.get_bind(), checkfirst=True)

    retention_enum = postgresql.ENUM(
        *RETENTION_VALUES, name='aria_data_stream_retention', create_type=False,
    )
    retention_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'aria_data_streams',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column(
            'domain',
            postgresql.ENUM(*DOMAIN_VALUES, name='aria_data_stream_domain', create_type=False),
            nullable=False,
        ),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('trigger_event', sa.String(length=255), nullable=False),
        sa.Column('payload_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('storage_table', sa.String(length=255), nullable=False),
        sa.Column(
            'retention_class',
            postgresql.ENUM(*RETENTION_VALUES, name='aria_data_stream_retention', create_type=False),
            nullable=False,
        ),
        sa.Column('transparency_visible', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )

    op.create_index('ix_aria_data_streams_domain', 'aria_data_streams', ['domain'])


def downgrade() -> None:
    op.drop_index('ix_aria_data_streams_domain', table_name='aria_data_streams')
    op.drop_table('aria_data_streams')
    postgresql.ENUM(name='aria_data_stream_retention').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='aria_data_stream_domain').drop(op.get_bind(), checkfirst=True)
