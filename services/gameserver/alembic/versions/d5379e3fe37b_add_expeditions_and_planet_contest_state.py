"""Add expeditions table and planet contest-state columns (ADR-0091).

WO lane1 — Planetary Survey & Site Discovery schema slice
(sw2102-docs/ADR/0091-site-discovery-landscape-citadel-grid.md).

  - ``expeditions`` — the at-launch ground-expedition roll record (ADR-0091
    §1/§3). No CandidateSite, no site_seed, no per-planet pre-stored result:
    ``result`` is the ephemeral SiteIntel payload on SUCCESS/PARTIAL, NULL
    on FAILURE/PENDING. ``demo`` marks the M39 free zero-stakes onboarding
    rolls.

  - ``planets`` additive/nullable columns for the ownership-contest state
    machine (ADR-0091 §8, M22/M28): ``contest_state``, ``deployer_id``,
    ``reserved_for_player_id`` (M40 onboarding sovereign-suppress),
    ``settled_at`` (R2 — ownership attaches at settle, not genesis-deploy),
    and ``native_life`` (§7 hazard-risk input, M54).

Retroactive scope is clean-apply (M28/GAP-4): there is no live game and no
players, so existing unclaimed planets simply carry NULL contest_state until
a follow-up backfill/resolver rollout (Wave-2, not this migration). Fully
additive — no drops, no data loss, no NOT NULL tightening.

Revision ID: d5379e3fe37b
Revises: b3f8e2a94c1d
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d5379e3fe37b"
down_revision = "b3f8e2a94c1d"
branch_labels = None
depends_on = None


# Full vocabularies declared up front so later slices never need a Postgres
# enum ALTER (codebase convention — see ship_status / npc_archetype). Values
# are the Python enum NAMES.
EXPEDITION_STATUS_VALUES = (
    "PENDING",
    "SUCCESS",
    "PARTIAL",
    "FAILURE",
)

PLANET_CONTEST_STATE_VALUES = (
    "FORMING",
    "PRIORITY",
    "OPEN",
    "SETTLE_LOCKED",
    "CLAIMED",
    "SUPPRESSED",
)


def upgrade() -> None:
    op.create_table(
        "expeditions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "planet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("planets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ship_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ships.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(*EXPEDITION_STATUS_VALUES, name="expedition_status"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "launched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "demo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_expeditions_player_id", "expeditions", ["player_id"])
    op.create_index("ix_expeditions_planet_id", "expeditions", ["planet_id"])

    op.add_column(
        "planets",
        sa.Column(
            "contest_state",
            sa.Enum(*PLANET_CONTEST_STATE_VALUES, name="planet_contest_state"),
            nullable=True,
        ),
    )
    op.add_column(
        "planets",
        sa.Column(
            "deployer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "planets",
        sa.Column(
            "reserved_for_player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "planets",
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "planets",
        sa.Column("native_life", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_planets_contest_state", "planets", ["contest_state"])


def downgrade() -> None:
    op.drop_index("ix_planets_contest_state", table_name="planets")
    op.drop_column("planets", "native_life")
    op.drop_column("planets", "settled_at")
    op.drop_column("planets", "reserved_for_player_id")
    op.drop_column("planets", "deployer_id")
    op.drop_column("planets", "contest_state")
    sa.Enum(name="planet_contest_state").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_expeditions_planet_id", table_name="expeditions")
    op.drop_index("ix_expeditions_player_id", table_name="expeditions")
    op.drop_table("expeditions")
    sa.Enum(name="expedition_status").drop(op.get_bind(), checkfirst=True)
