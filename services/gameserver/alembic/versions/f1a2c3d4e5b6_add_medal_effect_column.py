"""add nullable medals.effect JSONB column (WO-CG medal bespoke-effect layer)

DECISIONS.md:479 (``medal-effects-model``, ✅ Decided Max 2026-06-20) + the blessed
spec ``audit/design-briefs/medal-effects-spec.md`` (FINAL section authoritative):
medals grant bespoke per-medal gameplay effects. The catalog ships an ``effect``
dict per entry; this migration adds the matching column so ``seed_medals`` can
upsert it and ``medal_service.get_active_medal_bonuses`` can read it.

Purely **ADDITIVE / forward-only**: ONE nullable JSONB column on the existing
``medals`` catalog table. No change to any existing row's data (every row keeps
``effect = NULL`` until the next ``seed_medals`` upsert populates it from the
catalog). No backfill, no data migration. Chained onto the verified linear dev
head ``e864a8aaa392`` (WO-IL1 region-invites). Does NOT branch. Downgrade drops
the single column and leaves the rest of the schema untouched.

Revision ID: f1a2c3d4e5b6
Revises: e864a8aaa392
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f1a2c3d4e5b6'
down_revision = 'e864a8aaa392'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable JSONB — existing rows get NULL (cosmetic-only) with no
    # rewrite; populated by medal_catalog.seed_medals on the next startup.
    op.add_column(
        'medals',
        sa.Column('effect', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('medals', 'effect')
