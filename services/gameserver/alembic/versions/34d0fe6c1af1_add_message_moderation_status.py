"""add messages.moderation_status (WO-RT-MOD-AUDIT-KERNEL)

Additive nullable String(16) -- stops the moderation 'delete' action from
hard-deleting the row it just stamped moderated_at/moderated_by on, which
destroyed the audit trail canon requires (FEATURES/gameplay/messaging.md:
"Moderated messages remain in the database for the audit log even after
content removal").

Vocabulary NO-CANON, flagged for the human: NULL (visible, default) |
'deleted' (this WO). 'redacted' / 'blocked' are RESERVED for the
separately-gated MOD-CANON-ACTIONS action set (accept/redact/block +
reputation penalties) and are not written by any code yet.

Nullable, no backfill needed (NULL = every existing row's current, unmoderated
state). No destructive change.

Revision ID: 34d0fe6c1af1
Revises: 8b9aa2bd781d
Create Date: 2026-07-08
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '34d0fe6c1af1'
down_revision = '8b9aa2bd781d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('moderation_status', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'moderation_status')
