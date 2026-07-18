"""Add MessageBeacon charge-cell columns + backfill existing rows

WO-BEACON-LIFECYCLE. Additive only: two new nullable DateTime columns on
the existing `message_beacons` table -- no existing column altered, no
existing table structurally changed. Repurposes the EXISTING `expiry`
column's *meaning* only (application-layer -- see message_beacon.py's own
docstring), not its type/nullability; the sweep query that already scans
it (`expiry IS NOT NULL AND expiry < now`) is untouched by this migration.

Backfill: every EXISTING beacon row gets exactly one fresh 30-day charge
cell anchored to THIS MIGRATION'S apply time (not each row's own
`deployed_at`) -- anchoring to `deployed_at` would leave any beacon older
than ~37 days already past its new hard-delete deadline the moment this
migration lands, instantly orphaning/dropping it out from under its
deployer. Anchoring to "now" (a single `now()` evaluated once per
UPDATE statement, applied uniformly) guarantees every pre-existing beacon
starts this new lifecycle with a full, untouched 30-day cell + 7-day grace
window, matching the same `now + 30d` / `now + 37d` shape deploy() uses
for a brand new beacon.

Revision ID: 09d0c6e55927
Revises: b9a7404a2c20
Create Date: 2026-07-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '09d0c6e55927'
down_revision = 'b9a7404a2c20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'message_beacons',
        sa.Column('charge_expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        'message_beacons',
        sa.Column('last_charged_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Backfill -- one fresh 30d cell for every existing row, anchored to
    # this migration's own apply time (see module docstring for why not
    # deployed_at). `expiry` (repurposed hard-delete deadline) is
    # recomputed the same way deploy()/recharge() compute it:
    # charge_expires_at + 7d grace.
    op.execute(
        """
        UPDATE message_beacons
        SET charge_expires_at = now() + interval '30 days',
            last_charged_at = now(),
            expiry = now() + interval '37 days'
        """
    )


def downgrade() -> None:
    op.drop_column('message_beacons', 'last_charged_at')
    op.drop_column('message_beacons', 'charge_expires_at')
    # NOTE: `expiry` is left as the backfill set it -- downgrade does not
    # attempt to reconstruct each row's original pre-migration `expiry`
    # value (24h/7d/30d/NULL "never"), which this migration did not
    # preserve anywhere. A downgrade after this migration has been live
    # returns every existing beacon to the fixed-cell hard-delete deadline
    # already stored in `expiry`, not to the old expiry-choice semantics.
