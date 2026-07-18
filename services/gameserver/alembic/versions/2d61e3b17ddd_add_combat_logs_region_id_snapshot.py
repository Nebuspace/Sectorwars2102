"""add combat_logs.region_id_snapshot

WO-CMB-CLOG-SNAP-1: region deletion currently orphans combat audit rows --
CombatLog carries a sector_uuid FK (ondelete=SET NULL) but no region column,
despite DATA_MODELS/combat.md:59 (ADR-0050 SK24) requiring a region snapshot
populated at row creation so the audit trail survives a region regenerating
(force=true) or terminating out from under its sectors.

Additive: one new nullable UUID column, deliberately WITHOUT a ForeignKey to
regions.id -- an FK would either block the region delete this column exists
to survive, or SET NULL right alongside sector_uuid, defeating the point.
No backfill: existing combat_logs rows predate the snapshot and stay NULL
("sector unknown -- region was deleted" is already the documented read for
a NULL sector_uuid; a NULL region_id_snapshot on old rows is the same class
of gap, not a data-migration this WO takes on).

Revision ID: 2d61e3b17ddd
Revises: ba1e001a8e54
Create Date: 2026-07-09 00:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = '2d61e3b17ddd'
down_revision = 'ba1e001a8e54'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'combat_logs',
        sa.Column(
            'region_id_snapshot',
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('combat_logs', 'region_id_snapshot')
