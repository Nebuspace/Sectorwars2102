"""NPC runtime schema — scheduler/lifecycle columns, npc_rosters, npc_death_log

Living NPC System Phase 1 (sw2102-docs DATA_MODELS/npcs.md,
SYSTEMS/npc-scheduler.md, SYSTEMS/npc-lifecycle.md, ADR-0063):

  - ``npc_characters`` gains the scheduler/lifecycle columns deferred by
    the v1 static slice: home_region_id, current_activity,
    lifecycle_stage, daily_schedule, duty_role, engagement_eligible_at,
    promotion_pending_at, replaced_by_id, mentor_id, role_history,
    backstory, credits (TRADER wallet — canon-silent, pending decision).
    Lodging FKs stay deferred with the NPCBarracks/OutlawBase tables.

  - ``npc_rosters`` — per-anchor role targets Loop B maintains. Canon's
    UNIQUE (region_id, faction_code, role) is NOT applied: BANG emits
    multiple pirate rosters per region (one per holding anchor), so
    uniqueness lives on bang_roster_ref with a non-unique index on the
    canon triple. Divergence flagged for the docs repo.

  - ``npc_death_log`` — kill audit trail (KIA processing step 3).

  - Two new enums declared with their FULL canon vocabularies
    (npc_activity, npc_lifecycle_stage) so later slices never need a
    Postgres enum ALTER. The existing npc_archetype / npc_status enums
    already carry their full vocabularies (f8b2d4c61a35).

  Backfills (existing v1 NPC rows are live on dev):
  - home_region_id from the live sector's region (current_sector_id join).
  - current_activity = PATROL for non-KIA rows (static patrols),
    REASSIGNED for KIA rows (activity is meaningless post-mortem; canon
    has no dead-activity value).
  - lifecycle_stage = ACTIVE for living rows (established officers, not
    recruits), KIA for KIA rows.
  - engagement_eligible_at = spawned_at (ADR-0063 migration note:
    default created_at).

Revision ID: a9c4e7f21d83
Revises: b7e3a9d52c14
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a9c4e7f21d83'
down_revision = 'b7e3a9d52c14'
branch_labels = None
depends_on = None


# Full canon vocabulary (SYSTEMS/npc-lifecycle.md schedule blocks) —
# UPPERCASE name==value per codebase convention.
NPC_ACTIVITY_VALUES = (
    'SLEEP',
    'COMMUTE',
    'PATROL',
    'WORK_STATION',
    'SOCIALIZE',
    'DINE',
    'TRAIN',
    'PERSONAL',
    'RAID',
    'SURVEY',
    'ENGAGED',
    'REASSIGNED',
    'SHIFT_HANDOFF',
    'SHIFT_REROUTE',
    'ERROR_STRANDED',
)

NPC_LIFECYCLE_STAGE_VALUES = (
    'RECRUIT',
    'ACTIVE',
    'SENIOR',
    'DECORATED',
    'RETIRED',
    'KIA',
    'REASSIGNED',
)


def upgrade() -> None:
    npc_activity = sa.Enum(*NPC_ACTIVITY_VALUES, name='npc_activity')
    npc_lifecycle_stage = sa.Enum(
        *NPC_LIFECYCLE_STAGE_VALUES, name='npc_lifecycle_stage'
    )
    npc_activity.create(op.get_bind(), checkfirst=True)
    npc_lifecycle_stage.create(op.get_bind(), checkfirst=True)

    # --- npc_characters: scheduler/lifecycle columns ---
    op.add_column(
        'npc_characters',
        sa.Column(
            'home_region_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='CASCADE'),
            nullable=True,
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'current_activity',
            postgresql.ENUM(name='npc_activity', create_type=False),
            nullable=False,
            server_default='SLEEP',
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'lifecycle_stage',
            postgresql.ENUM(name='npc_lifecycle_stage', create_type=False),
            nullable=False,
            server_default='RECRUIT',
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'daily_schedule',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column('duty_role', sa.String(length=50), nullable=True),
    )
    op.add_column(
        'npc_characters',
        sa.Column('engagement_eligible_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'npc_characters',
        sa.Column('promotion_pending_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'replaced_by_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('npc_characters.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'mentor_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('npc_characters.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'role_history',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'backstory',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        'npc_characters',
        sa.Column(
            'credits',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )
    op.create_index(
        'ix_npc_characters_home_region_id',
        'npc_characters',
        ['home_region_id'],
    )

    # --- backfills for the v1 rows live on dev ---
    op.execute(
        """
        UPDATE npc_characters AS n
        SET home_region_id = s.region_id
        FROM sectors AS s
        WHERE n.current_sector_id IS NOT NULL
          AND s.sector_id = n.current_sector_id
        """
    )
    op.execute(
        """
        UPDATE npc_characters
        SET current_activity = CASE WHEN status = 'KIA'
                                    THEN 'REASSIGNED'::npc_activity
                                    ELSE 'PATROL'::npc_activity END,
            lifecycle_stage = CASE WHEN status = 'KIA'
                                   THEN 'KIA'::npc_lifecycle_stage
                                   ELSE 'ACTIVE'::npc_lifecycle_stage END,
            engagement_eligible_at = spawned_at
        """
    )

    # --- npc_rosters ---
    op.create_table(
        'npc_rosters',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'region_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('faction_code', sa.String(length=50), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column(
            'default_archetype',
            postgresql.ENUM(name='npc_archetype', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'schedule_template',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column('default_lodging_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('default_lodging_type', sa.String(length=20), nullable=True),
        sa.Column('target_count', sa.Integer(), nullable=False),
        sa.Column(
            'name_pool',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column('host_sector_id', sa.Integer(), nullable=False),
        sa.Column('bang_roster_ref', sa.String(length=80), nullable=False, unique=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        'ix_npc_rosters_region_faction_role',
        'npc_rosters',
        ['region_id', 'faction_code', 'role'],
    )

    # --- npc_death_log ---
    op.create_table(
        'npc_death_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'npc_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('npc_characters.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'killed_by_player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('sector_id', sa.Integer(), nullable=False),
        sa.Column(
            'home_region_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'combat_log_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('combat_logs.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('destruction_cause', sa.String(), nullable=True),
        sa.Column(
            'killed_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index('ix_npc_death_log_npc_id', 'npc_death_log', ['npc_id'])


def downgrade() -> None:
    op.drop_index('ix_npc_death_log_npc_id', table_name='npc_death_log')
    op.drop_table('npc_death_log')
    op.drop_index('ix_npc_rosters_region_faction_role', table_name='npc_rosters')
    op.drop_table('npc_rosters')

    op.drop_index('ix_npc_characters_home_region_id', table_name='npc_characters')
    op.drop_column('npc_characters', 'credits')
    op.drop_column('npc_characters', 'backstory')
    op.drop_column('npc_characters', 'role_history')
    op.drop_column('npc_characters', 'mentor_id')
    op.drop_column('npc_characters', 'replaced_by_id')
    op.drop_column('npc_characters', 'promotion_pending_at')
    op.drop_column('npc_characters', 'engagement_eligible_at')
    op.drop_column('npc_characters', 'duty_role')
    op.drop_column('npc_characters', 'daily_schedule')
    op.drop_column('npc_characters', 'lifecycle_stage')
    op.drop_column('npc_characters', 'current_activity')
    op.drop_column('npc_characters', 'home_region_id')

    sa.Enum(name='npc_lifecycle_stage').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='npc_activity').drop(op.get_bind(), checkfirst=True)
