"""PayPal failed-payment suspension -- consecutive-failure counters.

Additive, nullable-only:

- ``users.payment_failure_count`` (INTEGER) -- consecutive failed recurring
  payments for a Galactic Citizen subscription.
- ``regions.payment_failure_count`` (INTEGER) -- same, for a regional-owner
  subscription.

Both are incremented by paypal_service._handle_payment_failed and reset to 0
by any successful payment / renewal webhook. NULL on legacy rows reads as
"no failures recorded" (the service coerces NULL -> 0).

Revision ID: f3d8a1c65b74
Revises: e1b4f7c82a91
Create Date: 2026-08-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3d8a1c65b74'
down_revision = 'e1b4f7c82a91'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("payment_failure_count", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "regions",
        sa.Column("payment_failure_count", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("regions", "payment_failure_count")
    op.drop_column("users", "payment_failure_count")
