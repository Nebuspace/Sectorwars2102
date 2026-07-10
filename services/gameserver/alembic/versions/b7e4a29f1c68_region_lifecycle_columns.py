"""Region lifecycle columns -- ADR-0050 batch3 provisioning-lifecycle hardening

Adds the lifecycle/generation columns backing the extended 7-state
RegionStatus enum (active/suspended/grace/terminated/pending/
generation_corrupt/attachment_pending -- DATA_MODELS/galaxy.md:89,
SYSTEMS/region-lifecycle.md:669,790, ADR-0050 SK17/SK19/SK21/SK22).
Region.status itself stays a plain String(50) column (no DB-level enum) --
only the Python-side RegionStatus enum grew; no DDL is needed for the
status column, it already accepts arbitrary short strings.

generation_seed: canon (galaxy.md:93) marks this NOT NULL. Shipped
NULLABLE here per the additive-only migration rule -- a NOT NULL column
against existing region rows with no seed value on record would be
destructive. Flagged, not silently narrowed.

This is a schema/model foundation only -- consumer wiring for the new
states (npc_scheduler_service.py, pirate_ecosystem_service.py) is a
separate later WO.

Deploy order: alembic upgrade BEFORE the gameserver restart that ships
this revision's code.

Revision ID: b7e4a29f1c68
Revises: 9f1e216e2321
Create Date: 2026-07-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b7e4a29f1c68'
down_revision = '9f1e216e2321'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('regions', sa.Column('suspended_at', sa.TIMESTAMP(), nullable=True))
    op.add_column('regions', sa.Column('terminated_at', sa.TIMESTAMP(), nullable=True))
    op.add_column('regions', sa.Column('scheduled_hard_delete_at', sa.TIMESTAMP(), nullable=True))
    op.add_column('regions', sa.Column('generation_seed', sa.BigInteger(), nullable=True))
    op.add_column(
        'regions',
        sa.Column(
            'generation_phase_checksums',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default='{}',
        ),
    )


def downgrade() -> None:
    op.drop_column('regions', 'generation_phase_checksums')
    op.drop_column('regions', 'generation_seed')
    op.drop_column('regions', 'scheduled_hard_delete_at')
    op.drop_column('regions', 'terminated_at')
    op.drop_column('regions', 'suspended_at')
