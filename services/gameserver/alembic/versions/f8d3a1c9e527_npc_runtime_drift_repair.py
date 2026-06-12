"""NPC runtime drift repair — idempotent catch-up of partial state

One-off repair for the dev DB on interstitch, which entered the Living
NPC System work in an Alembic-untracked state: ``alembic_version`` did
not exist, ``npc_rosters`` / ``npc_death_log`` / ``pending_engagements``
plus their enums had been created by SQLAlchemy's ``Base.metadata.
create_all()`` (a stray pytest conftest pass — ``create_all`` makes the
new tables but NEVER ALTERs the pre-existing ``npc_characters`` /
``players`` / ``enhanced_market_transactions``), so the four NPC
migrations (a9c4e7f21d83 → c2d9e6f43a71 → e5f8a7c92d46 → f4a2b9c81d57)
were never run and re-running them would crash on "table already
exists".

Everything in here uses native idempotency — ``ADD COLUMN IF NOT
EXISTS``, ``CREATE INDEX IF NOT EXISTS``, ``Enum.create(checkfirst=True)``,
``CREATE TABLE IF NOT EXISTS`` — so on a freshly built DB (where
a9c4e7f21d83…f4a2b9c81d57 already supplied the schema) this migration
is a no-op, and on the drifted dev DB it adds exactly what's missing.

Deploy procedure on the drifted dev host (one-time):

    docker exec sectorwars-database psql -U postgres -d sectorwars_dev \\
        -c "CREATE TABLE IF NOT EXISTS alembic_version (
              version_num VARCHAR(32) PRIMARY KEY);
            INSERT INTO alembic_version (version_num) VALUES ('f4a2b9c81d57')
              ON CONFLICT DO NOTHING;"
    docker compose exec gameserver poetry run alembic upgrade head

Revision ID: f8d3a1c9e527
Revises: f4a2b9c81d57
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8d3a1c9e527'
down_revision = 'f4a2b9c81d57'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums (no-op when already present) ---
    sa.Enum(
        'SLEEP', 'COMMUTE', 'PATROL', 'WORK_STATION', 'SOCIALIZE', 'DINE',
        'TRAIN', 'PERSONAL', 'RAID', 'SURVEY', 'ENGAGED', 'REASSIGNED',
        'SHIFT_HANDOFF', 'SHIFT_REROUTE', 'ERROR_STRANDED',
        name='npc_activity',
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        'RECRUIT', 'ACTIVE', 'SENIOR', 'DECORATED', 'RETIRED', 'KIA',
        'REASSIGNED',
        name='npc_lifecycle_stage',
    ).create(op.get_bind(), checkfirst=True)
    sa.Enum(
        'PENDING', 'ARRIVED', 'RESOLVED', 'CANCELLED', 'EXPIRED',
        name='engagement_status',
    ).create(op.get_bind(), checkfirst=True)

    # --- npc_characters: 12 missing scheduler/lifecycle columns + index ---
    op.execute(
        """
        ALTER TABLE npc_characters
            ADD COLUMN IF NOT EXISTS home_region_id UUID
                REFERENCES regions(id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS current_activity npc_activity
                NOT NULL DEFAULT 'SLEEP',
            ADD COLUMN IF NOT EXISTS lifecycle_stage npc_lifecycle_stage
                NOT NULL DEFAULT 'RECRUIT',
            ADD COLUMN IF NOT EXISTS daily_schedule JSONB
                NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS duty_role VARCHAR(50),
            ADD COLUMN IF NOT EXISTS engagement_eligible_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS promotion_pending_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS replaced_by_id UUID
                REFERENCES npc_characters(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS mentor_id UUID
                REFERENCES npc_characters(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS role_history JSONB
                NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS backstory JSONB
                NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS credits INTEGER
                NOT NULL DEFAULT 0
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_npc_characters_home_region_id "
        "ON npc_characters(home_region_id)"
    )

    # --- Backfill v1 NPC rows (matches the original a9c4e7f21d83 logic,
    # restricted to rows that haven't been backfilled yet so this is
    # safe to re-run and a no-op on a fresh DB). ---
    op.execute(
        """
        UPDATE npc_characters AS n
        SET home_region_id = s.region_id
        FROM sectors AS s
        WHERE n.home_region_id IS NULL
          AND n.current_sector_id IS NOT NULL
          AND s.sector_id = n.current_sector_id
        """
    )
    op.execute(
        """
        UPDATE npc_characters
        SET current_activity = CASE
                WHEN status = 'KIA' THEN 'REASSIGNED'::npc_activity
                ELSE 'PATROL'::npc_activity
            END,
            lifecycle_stage = CASE
                WHEN status = 'KIA' THEN 'KIA'::npc_lifecycle_stage
                ELSE 'ACTIVE'::npc_lifecycle_stage
            END,
            engagement_eligible_at = COALESCE(engagement_eligible_at, spawned_at)
        WHERE engagement_eligible_at IS NULL
        """
    )

    # --- npc_rosters / npc_death_log / pending_engagements: created by
    # the partial state, but on a fresh DB these tables don't exist yet
    # when the repair runs (the upstream migrations already created them).
    # The guards make the migration a no-op in that case. ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS npc_rosters (
            id UUID PRIMARY KEY,
            region_id UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
            faction_code VARCHAR(50) NOT NULL,
            role VARCHAR(50) NOT NULL,
            default_archetype npc_archetype NOT NULL,
            schedule_template JSONB NOT NULL DEFAULT '{}'::jsonb,
            default_lodging_id UUID,
            default_lodging_type VARCHAR(20),
            target_count INTEGER NOT NULL,
            name_pool JSONB NOT NULL DEFAULT '{}'::jsonb,
            host_sector_id INTEGER NOT NULL,
            bang_roster_ref VARCHAR(80) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_npc_rosters_region_faction_role "
        "ON npc_rosters(region_id, faction_code, role)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS npc_death_log (
            id UUID PRIMARY KEY,
            npc_id UUID NOT NULL REFERENCES npc_characters(id) ON DELETE CASCADE,
            killed_by_player_id UUID REFERENCES players(id) ON DELETE SET NULL,
            sector_id INTEGER NOT NULL,
            home_region_id UUID REFERENCES regions(id) ON DELETE SET NULL,
            combat_log_id UUID REFERENCES combat_logs(id) ON DELETE SET NULL,
            destruction_cause VARCHAR,
            killed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_npc_death_log_npc_id "
        "ON npc_death_log(npc_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_engagements (
            id UUID PRIMARY KEY,
            player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            offense_type VARCHAR(40) NOT NULL,
            jurisdiction VARCHAR(20) NOT NULL,
            offense_sector_id INTEGER NOT NULL,
            region_id UUID REFERENCES regions(id) ON DELETE SET NULL,
            npc_squad_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            offense_at_turn_count INTEGER NOT NULL,
            arrival_turn_threshold INTEGER,
            status engagement_status NOT NULL DEFAULT 'PENDING',
            arrival_sector_id INTEGER,
            grace_expires_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_engagements_player_threshold "
        "ON pending_engagements(player_id, arrival_turn_threshold)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pending_engagements_status "
        "ON pending_engagements(status)"
    )

    # --- players.lifetime_turns_spent (c2d9e6f43a71) ---
    op.execute(
        "ALTER TABLE players "
        "ADD COLUMN IF NOT EXISTS lifetime_turns_spent INTEGER "
        "NOT NULL DEFAULT 0"
    )

    # --- enhanced_market_transactions.npc_id + index (f4a2b9c81d57) ---
    op.execute(
        "ALTER TABLE enhanced_market_transactions "
        "ADD COLUMN IF NOT EXISTS npc_id UUID "
        "REFERENCES npc_characters(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_market_transactions_npc_id "
        "ON enhanced_market_transactions(npc_id)"
    )


def downgrade() -> None:
    # No-op: this is a one-off drift repair. The columns/tables/indexes
    # it ensures are present are owned by upstream migrations
    # (a9c4e7f21d83, c2d9e6f43a71, e5f8a7c92d46, f4a2b9c81d57); dropping
    # them here would crash those when they run on a fresh DB.
    pass
