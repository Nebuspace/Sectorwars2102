"""ADR-0060 — pirate_holdings raid/capture kernel (G-F2/G-V1/R-F1).

Additive-only, nullable columns on ``pirate_holdings`` backing the dormant
raid/capture service kernel (see ``src/models/pirate_holding.py`` and
``src/services/pirate_ecosystem_service.py`` for the no-live-caller-yet
framing — awaits WO-PIRATE-ECO-3-ATTEMPT-CAPTURE):

- ``formation_id`` (UUID, FK ``special_formations.id`` SET NULL) — R-F1's
  CHECK constraint dependency (see below). Previously deferred per
  pirate_holding.py's module docstring; added here because R-F1 names it
  directly in the constraint text.
- ``combat_lock_held_by`` (UUID, FK ``players.id`` SET NULL) — G-F2 raid
  lock holder.
- ``combat_lock_team_snapshot`` (UUID[]) — G-F2 frozen team-mate snapshot,
  captured at first team-mate engagement.
- ``owner_team_id`` (UUID, FK ``teams.id`` SET NULL) — team-capture marker.
- ``captured_at`` (TIMESTAMPTZ) — capture timestamp.
- ``evolution_clock_started_at`` (TIMESTAMPTZ) — G-I1 evolution-clock reset
  anchor.

Plus R-F1's Stronghold-formation CHECK constraint, verbatim (tier enum
values are UPPERCASE in this codebase — see PirateHoldingTier):

    ALTER TABLE pirate_holdings
    ADD CONSTRAINT pirate_holdings_stronghold_requires_formation
    CHECK (tier != 'STRONGHOLD' OR formation_id IS NOT NULL);

Revision ID: d7a3e6f19c52
Revises: c4f9a2e17b83
Create Date: 2026-08-07 00:00:00.000000
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d7a3e6f19c52"
down_revision = "c4f9a2e17b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pirate_holdings",
        sa.Column(
            "formation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("special_formations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_pirate_holdings_formation_id", "pirate_holdings", ["formation_id"]
    )

    op.add_column(
        "pirate_holdings",
        sa.Column(
            "combat_lock_held_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "pirate_holdings",
        sa.Column(
            "combat_lock_team_snapshot",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )
    op.add_column(
        "pirate_holdings",
        sa.Column(
            "owner_team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_pirate_holdings_owner_team_id", "pirate_holdings", ["owner_team_id"]
    )
    op.add_column(
        "pirate_holdings",
        sa.Column("captured_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "pirate_holdings",
        sa.Column(
            "evolution_clock_started_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
    )

    op.create_check_constraint(
        "pirate_holdings_stronghold_requires_formation",
        "pirate_holdings",
        "tier != 'STRONGHOLD' OR formation_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "pirate_holdings_stronghold_requires_formation",
        "pirate_holdings",
        type_="check",
    )
    op.drop_column("pirate_holdings", "evolution_clock_started_at")
    op.drop_column("pirate_holdings", "captured_at")
    op.drop_index("ix_pirate_holdings_owner_team_id", table_name="pirate_holdings")
    op.drop_column("pirate_holdings", "owner_team_id")
    op.drop_column("pirate_holdings", "combat_lock_team_snapshot")
    op.drop_column("pirate_holdings", "combat_lock_held_by")
    op.drop_index("ix_pirate_holdings_formation_id", table_name="pirate_holdings")
    op.drop_column("pirate_holdings", "formation_id")
