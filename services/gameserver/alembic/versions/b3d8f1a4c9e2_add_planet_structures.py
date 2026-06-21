"""add nullable Planet.structures JSONB (CRT grid spine, WO-K1a-1)

Additive/nullable only — no ALTER of the existing nullable=False ``active_events`` column. A planet
with NULL ``structures`` is a legacy planet that ``structures.seed()`` cold-starts on first
``settle()``; the spine's monotonic gate (``terraform_meta.last_settle_at``) and the K1b grid
layout live under this key. Mirrors the ``a4d8e2f91b67`` ships.hangar additive-JSONB pattern (no
server_default — the populated shape is an application concern, not a DB default).

Revision ID: b3d8f1a4c9e2
Revises: a7c9e2f1b8d4
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b3d8f1a4c9e2'
down_revision = 'a7c9e2f1b8d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'planets',
        sa.Column('structures', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('planets', 'structures')
