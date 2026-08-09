"""Add Ship pending hatch-pin reset columns
(WO-BUILD-SHIP-PIN-PORT-RESET-DELAYED)

Purely additive: two new nullable columns on ``ships``, no existing column
or table touched. Canon: SYSTEMS/ship-registry.md "Hatch pin lock" -- "the
registered owner can always reset the pin via a port admin action (1-hour
real-time delay before the new pin takes effect; gives any current borrower
a window to extract themselves before being locked out)". Mirrors the
pending_transfer_* additive-columns pattern from
c4a8e2f7b910_ship_registry_contested_transfer.py.

Revision ID: a8bb89ee1002
Revises: cdcf5260b3a6
Create Date: 2026-08-08 21:03:43.910923

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8bb89ee1002'
down_revision = 'cdcf5260b3a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ships",
        sa.Column("pending_hatch_pin_code", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "ships",
        sa.Column("pending_hatch_pin_effective_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ships", "pending_hatch_pin_effective_at")
    op.drop_column("ships", "pending_hatch_pin_code")
