"""Phantom-TABLE catch-up: 8 tables that only ever existed via startup
Base.metadata.create_all, never via migration (WO-QTI-PHANTOM-TABLE-CATCHUP).

Root cause (orchestrator's CI schema-parity gate, first true-positive run):
main.py + start.sh run `Base.metadata.create_all` at startup, which silently
creates any model-declared table the migration chain never caught up to.
Every long-lived dev DB (including heimdall, the live parity-check target)
picked these up that way and was never actually "pristine" -- but a genuinely
fresh alembic-only deploy (and the CI gate, which builds one) lacks them
entirely. This migration makes the migration chain authoritative for exactly
these 8 tables, matching their models byte-for-byte by reusing SQLAlchemy's
own table DDL (`Base.metadata.create_all(tables=[...])`) instead of hand-
transcribed `op.create_table` blocks -- 8 tables' worth of FKs/indexes/
uniques by hand is too error-prone and must match exactly what the live
parity tool checks against the ORM registry.

The 8 tables (grep-verified against their model files, each still carrying
its own "no Alembic migration is needed" docstring claim -- now false):
  - docking.py            -> DockingSlipOccupancy ("docking_slip_occupancies")
  - docking.py            -> DockingQueueEntry ("docking_queue_entries")
  - port_ownership.py     -> StationListing ("station_listings")
  - port_ownership.py     -> PurchaseOffer ("station_purchase_offers")
  - port_ownership.py     -> TakeoverCampaign ("station_takeover_campaigns")
  - player_analytics.py   -> PlayerSession ("player_sessions")
  - player_analytics.py   -> PlayerAnalyticsSnapshot ("player_analytics_snapshots")
  - player_analytics.py   -> PlayerActivity ("player_activities")

NOT included: player_analytics.py also declares PlayerReEngagement
("player_re_engagement_queue") -- that one already has its own migration
(c9f2e7a41d83) and is deliberately excluded here.

Enum census: none of these 8 tables declare a SQLAlchemy `Enum` column --
every status-like field (StationListing.status, PurchaseOffer.status,
TakeoverCampaign.status, DockingSlipOccupancy.slip_class) is a plain
`String` column with an application-level vocabulary, not a native PG enum.
`create_all(tables=[...])` therefore has no enum-type dependency to resolve
for this batch -- verified by reading all 8 model definitions directly, not
inferred.

Internal FK dependency edges among the 8 (both PurchaseOffer and
PlayerActivity reference a sibling table in this same batch, not just
players/stations):
  - PurchaseOffer.listing_id     -> station_listings.id (StationListing)
  - PlayerActivity.session_id    -> player_sessions.id (PlayerSession)
`create_all`/`drop_all` topologically sort the given `tables=` list by FK
dependency internally regardless of list order, so no manual ordering is
needed in either direction here.

IDEMPOTENT BY DESIGN: `checkfirst=True` on both create_all and drop_all
makes this migration a no-op wherever a table already exists (every
create_all-healed DB, including heimdral/dev/stage) and a genuine create/
drop everywhere else (a fresh alembic-only build, including the CI gate).
This migration must COEXIST with startup create_all -- removing create_all-
at-startup is a separate, Max-design-gated item tracked elsewhere; this
migration does not touch main.py or start.sh.

Revision ID: 9f1e216e2321
Revises: 7643ee82d04b
Create Date: 2026-07-10 01:50:37.254993

"""
from alembic import op

from src.core.database import Base
from src.models.docking import DockingQueueEntry, DockingSlipOccupancy
from src.models.player_analytics import (
    PlayerActivity,
    PlayerAnalyticsSnapshot,
    PlayerSession,
)
from src.models.port_ownership import (
    PurchaseOffer,
    StationListing,
    TakeoverCampaign,
)

# revision identifiers, used by Alembic.
revision = '9f1e216e2321'
down_revision = '7643ee82d04b'
branch_labels = None
depends_on = None

_CATCHUP_TABLES = [
    DockingSlipOccupancy.__table__,
    DockingQueueEntry.__table__,
    StationListing.__table__,
    PurchaseOffer.__table__,
    TakeoverCampaign.__table__,
    PlayerSession.__table__,
    PlayerAnalyticsSnapshot.__table__,
    PlayerActivity.__table__,
]


def upgrade() -> None:
    # checkfirst=True: no-op on any DB where startup create_all already
    # built these (every long-lived dev/stage DB today); a genuine create
    # on a fresh alembic-only build (the CI gate).
    Base.metadata.create_all(bind=op.get_bind(), tables=_CATCHUP_TABLES, checkfirst=True)


def downgrade() -> None:
    # checkfirst=True: safe even if a table was never actually created by
    # this migration (e.g. downgrading a DB where startup create_all is
    # still the one that will immediately re-create it on next boot).
    Base.metadata.drop_all(bind=op.get_bind(), tables=_CATCHUP_TABLES, checkfirst=True)
