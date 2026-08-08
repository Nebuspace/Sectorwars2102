"""Add region-termination-cascade prepay flags (WO-BUILD-REGION-LIFECYCLE-CLEANUP-CASCADE, reduced scope).

Adds ``Planet.transport_prepaid`` (ADR-0050 "Planet-safe transport paths" B:
player pre-pays the 20% transport fee during Suspended/Grace so the safe
transfers at 100% on cascade) and ``Station.relocation_prepaid`` (ADR-0050
"Station relocation paths" B: same pre-pay pattern for the 30% relocation
fee). Both flags are asset-scoped (per-planet / per-station), not
region-scoped, because a single region can hold many planets/stations that
each independently choose to pre-pay -- a region-level flag could not
represent that. ``relocation_prepaid`` ships now (schema-only) alongside its
sibling column even though the station-relocation cascade itself remains a
discovery-only stub (see region_termination_cascade_service.py) pending
Station.acquisition_cost / upgrade-capital-cost tracking, which does not
exist anywhere in the schema yet -- the flag is cheap, additive, and avoids a
second migration once that blocker clears.

Fully additive -- two new nullable Boolean columns only, no existing column
changes.

Revision ID: c4d8e61f97ab
Revises: a3f7c91e05b8
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4d8e61f97ab"
down_revision = "a3f7c91e05b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "planets",
        sa.Column("transport_prepaid", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "stations",
        sa.Column("relocation_prepaid", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stations", "relocation_prepaid")
    op.drop_column("planets", "transport_prepaid")
