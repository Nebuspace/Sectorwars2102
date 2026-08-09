"""ADR-0054 X-D3 -- GC-lapse 7-day liquidation window columns on players.

Additive, nullable-only:

- ``gc_lapsed_at`` (TIMESTAMPTZ) -- stamped by paypal_service.
  _handle_subscription_cancelled when a Galactic Citizen subscription is
  cancelled; NULL means the player is not currently in a lapse window.
  ``is_galactic_citizen`` is left True through the 7-day grace; the
  scheduler sweep (economy_sweeps -- ``_run_gc_lapse_sweep_sync``) flips it
  False once the window elapses with no re-subscription.
- ``gc_relocation_used_at`` (TIMESTAMPTZ) -- one-time GC-bypass emergency
  relocation consumption marker, cleared alongside ``gc_lapsed_at`` on
  re-subscription so the grant renews for the next lapse cycle.

Revision ID: e1b4f7c82a91
Revises: d7a3e6f19c52
Create Date: 2026-08-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1b4f7c82a91'
down_revision = 'd7a3e6f19c52'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("gc_lapsed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "players",
        sa.Column("gc_relocation_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "gc_relocation_used_at")
    op.drop_column("players", "gc_lapsed_at")
