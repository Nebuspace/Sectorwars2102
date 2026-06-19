"""widen player_owned region sector cap 1000 -> 1500

Relaxes the 'valid_region_type_sector_count' CHECK on regions so player_owned
regions may have up to 1500 total_sectors (was 1000). Aligns the schema with
canon: DATA_MODELS/galaxy.md and ADR-0050 both state the per-region cap is
CHECK 100-1500. The central_nexus = 5000 and terran_space = 300 clauses are
preserved verbatim.

ADDITIVE / non-destructive: the new check is strictly WIDER than the old one,
so no existing row can fail it. Done as drop + re-add of the named CHECK because
Postgres has no in-place ALTER for a CHECK condition.

Revision ID: b4d2f7e9a1c6
Revises: a1d4f9c7b3e2
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b4d2f7e9a1c6'
down_revision = 'a1d4f9c7b3e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('valid_region_type_sector_count', 'regions', type_='check')
    op.create_check_constraint(
        'valid_region_type_sector_count',
        'regions',
        "(region_type != 'central_nexus' OR total_sectors = 5000) AND "
        "(region_type != 'terran_space' OR total_sectors = 300) AND "
        "(region_type != 'player_owned' OR (total_sectors >= 100 AND total_sectors <= 1500))",
    )


def downgrade() -> None:
    op.drop_constraint('valid_region_type_sector_count', 'regions', type_='check')
    op.create_check_constraint(
        'valid_region_type_sector_count',
        'regions',
        "(region_type != 'central_nexus' OR total_sectors = 5000) AND "
        "(region_type != 'terran_space' OR total_sectors = 300) AND "
        "(region_type != 'player_owned' OR (total_sectors >= 100 AND total_sectors <= 1000))",
    )
