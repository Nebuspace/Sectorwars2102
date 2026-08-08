"""Construction events -- roll-cadence anchor column.

Additive, nullable-only:

- ``construction_reservations.events_last_rolled_at`` (TIMESTAMPTZ) -- wall-
  clock instant construction-event RNG (construction_service.roll_construction_event)
  was last rolled through for this reservation, mirroring the accrual-anchor
  pattern already used by port_ownership_service.accrue_operating_costs for
  the same "lazy engine, whole elapsed canonical days" shape. NULL means
  "never rolled yet" -- the service anchors it to `now` on first eligibility
  rather than backdating to reservation creation, so no reservation gets a
  retroactive event backlog the moment this ships.

Revision ID: cdcf5260b3a6
Revises: f3d8a1c65b74
Create Date: 2026-08-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cdcf5260b3a6'
down_revision = 'f3d8a1c65b74'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "construction_reservations",
        sa.Column("events_last_rolled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("construction_reservations", "events_last_rolled_at")
