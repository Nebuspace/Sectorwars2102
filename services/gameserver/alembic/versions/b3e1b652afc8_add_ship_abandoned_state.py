"""Add Ship.is_abandoned / abandoned_at (WO-GWQ-STRANDING-2)

Purely additive: two new nullable-friendly columns on ``ships``, no existing
column or table touched.

Canon DATA_MODELS/ships.md:21,51-52 already names both fields for the
port-abandonment/free-claim feature (not yet built). This WO's escape-pod
egress from a WARP_SINK (lane A) is a SECOND producer of the same state:
a stranded pilot's free pod ejection leaves the hull behind, undestroyed,
marked abandoned so it persists as a recoverable derelict instead of
vanishing. Only the state marker ships here -- the free-claim /
7-day-auto-archive BEHAVIOR canon describes for the port-abandonment case
is a separate, larger feature and is not built by this migration.

Revision ID: b3e1b652afc8
Revises: e8b619503a44
Create Date: 2026-07-10 02:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3e1b652afc8'
down_revision = 'e8b619503a44'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'ships',
        sa.Column(
            'is_abandoned',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.add_column(
        'ships',
        sa.Column('abandoned_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ships', 'abandoned_at')
    op.drop_column('ships', 'is_abandoned')
