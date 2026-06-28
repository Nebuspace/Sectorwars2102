"""payments: processed_webhook_events idempotency ledger (ADR-0058)

Additive new table for webhook exactly-once processing. One row per provider
event id; the unique primary key lets the webhook handler insert-as-dedup in the
same transaction as the subscription mutation, so duplicate PayPal deliveries are
skipped rather than re-applied.

Revision ID: d3f7a91c2b84
Revises: c2e6b9d4f7a1
Create Date: 2026-06-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3f7a91c2b84'
down_revision = 'c2e6b9d4f7a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'processed_webhook_events',
        sa.Column('event_id', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade() -> None:
    op.drop_table('processed_webhook_events')
