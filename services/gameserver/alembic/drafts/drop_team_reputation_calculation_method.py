"""DRAFT ONLY — DO NOT APPLY without human sign-off.

WO-CLEANUP-TEAM-REPUTATION-CALC-METHOD-DEAD-COLUMN (cycle-44).

Drops the deprecated ``teams.reputation_calculation_method`` column.
``TeamReputation.calculation_method`` is the sole SSOT
(DECISIONS.md team-reputation-calculation-method-canonical, 2026-08-04).

Before apply:
1. Confirm zero remaining writers/readers of Team.reputation_calculation_method
   (team_reputation_service.py already documents the deprecation).
2. Explicit human GO (destructive migration).
3. Move into alembic/versions/ with a real down_revision at then-current head.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers — PLACEHOLDERS until human GO + real head pin
revision = "DRAFT_drop_team_rep_calc_method"
down_revision = None  # pin to live head only when authorized to apply
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("teams", "reputation_calculation_method")


def downgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "reputation_calculation_method",
            sa.String(length=20),
            nullable=False,
            server_default="AVERAGE",
        ),
    )
