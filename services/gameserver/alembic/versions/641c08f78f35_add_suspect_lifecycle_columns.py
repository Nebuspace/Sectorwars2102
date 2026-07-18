"""Add Player.suspect_until / suspect_team_snapshot (WO-CMB-SUSPECT-LIFE-1)

Purely additive: two new nullable columns on ``players``, no existing column
or table touched. ``is_suspect`` / ``suspect_declared_at`` (already shipped)
are unchanged in shape -- ``suspect_declared_at`` is repurposed in code
(not schema) as the first-acquisition anchor for the new 4h cumulative cap.

Canon: sw2102-docs/FEATURES/gameplay/ships.md:287-296 (the salvage-grace
Suspect mechanic) + ADR-0061 S-V4 (team-membership snapshot semantics) +
DATA_MODELS/player.md's target schema, which names the column
``suspect_team_snapshot`` (this migration uses that exact name, not the
work order's shorthand "suspect_team_ids").

Revision ID: 641c08f78f35
Revises: 1aab831e9008
Create Date: 2026-07-10 02:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '641c08f78f35'
down_revision = '1aab831e9008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'players',
        sa.Column('suspect_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'players',
        sa.Column(
            'suspect_team_snapshot',
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('players', 'suspect_team_snapshot')
    op.drop_column('players', 'suspect_until')
