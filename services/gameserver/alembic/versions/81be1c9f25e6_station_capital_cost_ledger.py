"""Add stations.capital_cost_ledger (WO-BUILD-STATION-ACQUISITION-COST-CAPITAL-LEDGER).

Additive only: new nullable JSONB column on stations. acquisition_cost
itself is not a new column -- it already lives in stations.ownership
['acquisition_cost'] (port_ownership_service._acquisition_cost). This
migration adds the other half of ADR-0050's relocation-fee formula: an
append-only ledger of one-time upgrade capital spend, per station.

Revision ID: 81be1c9f25e6
Revises: d1e4a7b2c908
Create Date: 2026-08-04 20:58:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "81be1c9f25e6"
down_revision = "d1e4a7b2c908"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column(
            "capital_cost_ledger",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("stations", "capital_cost_ledger")
