"""planet landing-rights ACL column (WO-G16)

Adds ``planets.landing_rights`` (JSONB, nullable) — the per-planet landing
access-control list (FEATURES/planets/colonization.md "Landing rights").

  Shape: {"mode": "public|team_only|private|whitelist|denylist",
          "whitelist": [player_uuid,...], "denylist": [player_uuid,...]}.

Additive only: a single nullable column on the planets table. NULL ⇒ public
(anyone may land), so every existing planet keeps its current open-landing
behavior with no backfill. Enforced at land-time only (no eviction); the
separate ``tax_rate`` axis is intentionally untouched (Max-gated).

Revision ID: d1a4f8c2e706
Revises: c9f2e7a41d83
Create Date: 2026-06-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd1a4f8c2e706'
down_revision = 'c9f2e7a41d83'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'planets',
        sa.Column('landing_rights', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('planets', 'landing_rights')
