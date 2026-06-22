"""planet abandonment / reclaim markers + inert tax_rate column (PL4b)

Adds three additive, NULLABLE columns to ``planets`` (PL4b — planet-level
abandonment/reclamation + the deferred tax axis):

  - ``tax_rate``       Float       NULL  -- inert this slice (column-only). NULL⇒0.0.
                                            The taxable event (a team-mate
                                            withdrawing stockpiled resources to
                                            ship cargo) has NO route in code yet,
                                            so NO tax logic is wired — only the
                                            clamp-bound [0.00, 0.20] is documented.
                                            The mechanic is a follow-on WO.
  - ``reclaimable_at`` TIMESTAMPTZ NULL  -- inactivity flag marker. NULL⇒not
                                            flagged. Set by the daily idempotent
                                            scheduler sweep when the owner's
                                            ``Player.last_game_login`` is older
                                            than INACTIVITY_DAYS=90; cleared the
                                            moment the owner logs back in. A
                                            RECLAIM_GRACE_DAYS=7 window after the
                                            flag gives the returning owner
                                            deterministic priority before any
                                            reclaim transaction may land.
  - ``abandoned_at``   TIMESTAMPTZ NULL  -- audit stamp for the moment a planet
                                            reverted to unowned (either the
                                            voluntary POST /abandon or the
                                            involuntary inactivity reclaim). Pure
                                            forensics; no behavior keys off it.

Additive, nullable-only, single-writer: every existing planet keeps its current
behavior with ZERO backfill (I1 — NULL tax_rate == 0.0; NULL flags == not
flagged). The reverse drops the three columns cleanly.

NOTE (single-head chaining): the branch ``feat/living-npc-system`` had exactly
ONE alembic head at author time — ``e2c1f9a7b4d6`` (add CITIZEN_CLIPPER ship
type). This migration chains onto that head so it does NOT create a spurious
independent head. (The PL4b master warned of multiple heads; on the live tree
only the single ``e2c1f9a7b4d6`` head existed, so this is a strict linear
extension of it.)

Revision ID: d7a2f1c9e3b5
Revises: e2c1f9a7b4d6
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a2f1c9e3b5'
down_revision = 'e2c1f9a7b4d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tax_rate: inert this slice (the skim has no taxable event in code yet).
    # NULL ⇒ 0.0; the clamp [0.00, 0.20] is enforced by the future setter, not
    # the column DDL (no CHECK constraint — keep the migration purely additive so
    # legacy rows with NULL stay valid and the deferred mechanic can pick the
    # enforcement layer).
    op.add_column('planets', sa.Column('tax_rate', sa.Float(), nullable=True))
    # Inactivity-reclamation flag marker (NULL ⇒ not flagged).
    op.add_column('planets', sa.Column('reclaimable_at', sa.DateTime(timezone=True), nullable=True))
    # Audit stamp for the moment ownership reverted (voluntary or involuntary).
    op.add_column('planets', sa.Column('abandoned_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('planets', 'abandoned_at')
    op.drop_column('planets', 'reclaimable_at')
    op.drop_column('planets', 'tax_rate')
