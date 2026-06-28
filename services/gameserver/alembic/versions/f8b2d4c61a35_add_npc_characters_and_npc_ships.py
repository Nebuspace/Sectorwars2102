"""add npc_characters table and NPC ship support

First NPCs slice (pirate captains v1, DATA_MODELS/npcs.md /
SYSTEMS/npc-scheduler.md — both Design-only; this is the documented
static v1 subset):

  - ``ships.owner_id`` becomes nullable — NPC-piloted hulls have no
    Player owner (the pilot is an ``NPCCharacter`` row, ship_id FK).

  - ``ships.is_npc`` — instance flag marking NPC-piloted ships so
    player-facing queries can exclude them. Minimal v1 analogue of
    canon's ``ShipSpecification.is_npc_only`` (DATA_MODELS/ships.md),
    used instead of new NPC hull types because canon defines no pirate
    hull stats yet.

  - ``npc_characters`` — v1 column subset of the canon NPCCharacter
    schema (identity, faction, archetype, status, location, piloted
    ship, lifecycle timestamps). Deferred canon columns: home_region_id,
    daily_schedule, role_history, lifecycle_stage, lodging FKs,
    mentor/succession chain, backstory. ``bang_roster_ref`` is a v1
    idempotency marker pending the canon NPCRoster table.

Revision ID: f8b2d4c61a35
Revises: e7a1c5d92b40
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f8b2d4c61a35'
down_revision = 'e7a1c5d92b40'
branch_labels = None
depends_on = None


# Full canon vocabularies (DATA_MODELS/npcs.md) declared up front so the
# scheduler slices never need a Postgres enum ALTER. Values are the Python
# enum NAMES (codebase convention — see ship_status / ship_type).
# Ten archetypes: npcs.md prose says "nine" but its enum section lists ten
# (STATION_SECURITY included) — canon-internal conflict flagged for the docs
# repo; the enum section wins here.
NPC_ARCHETYPE_VALUES = (
    'LAW_ENFORCEMENT',
    'FACTION_PATROL',
    'HOSTILE_RAIDER',
    'FACTION_LEADER',
    'STATION_OFFICIAL',
    'STATION_SECURITY',
    'MISSION_GIVER',
    'TRADER',
    'RESEARCHER',
    'CIVILIAN',
)

# RESPAWNING covers npc-scheduler.md "KIA processing" step 9, in tension
# with npcs.md ADR-0063 N-D2 (permanent KIA) — canon gap, not resolved here.
NPC_STATUS_VALUES = (
    'ON_DUTY',
    'OFF_DUTY',
    'ENGAGED',
    'ENGAGED_PENDING_ARRIVAL',
    'KIA',
    'RESPAWNING',
    'RETIRED',
    'REASSIGNED',
)


def upgrade() -> None:
    op.alter_column(
        'ships',
        'owner_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        'ships',
        sa.Column(
            'is_npc',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )

    op.create_table(
        'npc_characters',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=50), nullable=True),
        sa.Column('faction_code', sa.String(length=50), nullable=False),
        sa.Column(
            'archetype',
            sa.Enum(*NPC_ARCHETYPE_VALUES, name='npc_archetype'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(*NPC_STATUS_VALUES, name='npc_status'),
            nullable=False,
            server_default='ON_DUTY',
        ),
        # Global sectors.sector_id (globally unique in this schema); canon's
        # region-local compound identifier lands with home_region_id later.
        sa.Column('current_sector_id', sa.Integer(), nullable=True),
        sa.Column(
            'ship_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('ships.id', ondelete='SET NULL'),
            nullable=True,
        ),
        # "<galaxy id>:<region_type>:<bang roster id>" — galaxy-scoped
        sa.Column('bang_roster_ref', sa.String(length=80), nullable=True),
        sa.Column(
            'spawned_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'last_seen_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('destroyed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('respawn_eligible_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        'ix_npc_characters_faction_code',
        'npc_characters',
        ['faction_code'],
    )
    op.create_index(
        'ix_npc_characters_current_sector_id',
        'npc_characters',
        ['current_sector_id'],
    )
    op.create_index(
        'ix_npc_characters_bang_roster_ref',
        'npc_characters',
        ['bang_roster_ref'],
    )


def downgrade() -> None:
    op.drop_index('ix_npc_characters_bang_roster_ref', table_name='npc_characters')
    op.drop_index('ix_npc_characters_current_sector_id', table_name='npc_characters')
    op.drop_index('ix_npc_characters_faction_code', table_name='npc_characters')
    op.drop_table('npc_characters')
    sa.Enum(name='npc_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='npc_archetype').drop(op.get_bind(), checkfirst=True)

    # NPC presence residue lives in sector JSONB — strip it before deleting
    # the NPC ships so a downgraded database doesn't render ghost contacts
    # or phantom patrol squads for the rows removed below.
    op.execute(
        """
        UPDATE sectors
        SET players_present = (
            SELECT COALESCE(jsonb_agg(e), '[]'::jsonb)
            FROM jsonb_array_elements(players_present) e
            WHERE NOT COALESCE((e->>'is_npc')::boolean, false)
        )
        """
    )
    op.execute("UPDATE sectors SET defenses = defenses - 'pirate_patrol_ships'")

    # Owner-less rows (NPC ships) would violate the restored NOT NULL —
    # remove them before tightening the constraint. Destructive by
    # necessity; player ships always have an owner and are untouched.
    op.execute('DELETE FROM ships WHERE owner_id IS NULL')
    op.drop_column('ships', 'is_npc')
    op.alter_column(
        'ships',
        'owner_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
