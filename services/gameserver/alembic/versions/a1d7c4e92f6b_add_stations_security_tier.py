"""add stations.security tier JSONB (WO-CB1, station-protection slice 1)

First slice of the station-protection system (FEATURES/economy/
station-protection.md): persist a per-station SECURITY TIER so the combat
resolver can honor Guarantee #1 — "for any ship docked at a station with
security_level >= basic, the combat-resolver rejects external attack attempts
with ERR_DOCKED_SHIP_PROTECTED."

ONE additive nullable JSONB column:
  * security  (JSONB, nullable)  — carries the tier under the "tier" key, one
    of four ordered levels: none(0) < basic(1) < standard(2) < premium(3).

NULL → "none" SEMANTICS (NO-CANON micro-decision, orchestrator-blessed
pending): an UNCONFIGURED station — security NULL, or a JSONB dict with no
"tier" key — reads as security_level "none", i.e. NOT protected. This is
deliberately conservative: existing/populated rows get NO new protection until
a station is explicitly seeded, so there is NO surprise behavior flip on live
data. Accordingly this migration is ADDITIVE ONLY — a nullable column with NO
backfill and NO destructive operation. Canon tier DEFAULTS (player-owned→basic,
operator-managed→standard/premium, frontier/lawless→none) are seeded by the
larger station-protection system, NOT by this migration.

Revision ID: a1d7c4e92f6b
Revises: c5a9f1e6b34d
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a1d7c4e92f6b'
down_revision = 'c5a9f1e6b34d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'stations',
        sa.Column('security', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('stations', 'security')
