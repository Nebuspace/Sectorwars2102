"""Phase-4 schema foundation: additive columns, new tables, faction_type enum values.

Owns ALL the model/migration schema changes for the Phase-4 build so the service
lanes that follow build on stable columns/tables. Everything here is ADDITIVE and
idempotent; it runs on dev WITH DATA.

Chained linearly off the single verified head a7c3e1f9b264 (no new branch).

COLUMNS (ADD COLUMN IF NOT EXISTS, idempotent):
  players: last_turn_regeneration (TIMESTAMPTZ null), max_turns (INT NOT NULL
    DEFAULT 1000), is_suspect / is_wanted / is_game_complete (BOOL NOT NULL
    DEFAULT false), suspect_declared_at / wanted_declared_at / rank_victory_at
    (TIMESTAMPTZ null)                                                  [ADR-0004]
  ships + ship_specifications: shield_resistance / armor_rating
    (DOUBLE PRECISION NOT NULL DEFAULT 0)                  [combat resolver storage]
  fleets: coordination_bonus (DOUBLE PRECISION NOT NULL DEFAULT 0)

NEW TABLES (created only IF NOT absent — create_table is naturally a no-op-safe
INSERT-once because a second run would error; guarded with an inspector check):
  sector_faction_influence                                              [ADR-0021]
  medals + player_medals                                               [ADR-0028]
  bounty_claims (+ bounty_claim_status enum)

ENUM faction_type: ADD VALUE IF NOT EXISTS for MINING, OUTLAWS, SYNDICATE,
CONCORD (ADR-0033 + Galactic Concord). These run in an
``op.get_context().autocommit_block()`` because Postgres forbids ALTER TYPE ...
ADD VALUE inside a transaction block. The block commits the column/table work
above, runs the enum appends with AUTOCOMMIT isolation, then opens a fresh
transaction. (Precedent: d4f7a2c91e58.) MILITARY is intentionally NOT touched
(code-wins — it predates the ADR-0033 enum table).

Revision ID: a1d4f9c7b3e2
Revises: a7c3e1f9b264
Create Date: 2026-06-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a1d4f9c7b3e2'
down_revision = 'a7c3e1f9b264'
branch_labels = None
depends_on = None


# faction_type enum values to append (MILITARY left untouched — code-wins).
NEW_FACTION_TYPE_VALUES = ('Mining', 'Outlaws', 'Syndicate', 'Concord')

BOUNTY_CLAIM_STATUS_VALUES = ('claimed', 'paid', 'cancelled', 'refunded')


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Additive columns (IF NOT EXISTS — transactional, idempotent).
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_turn_regeneration TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS max_turns INTEGER NOT NULL DEFAULT 1000")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_suspect BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_wanted BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS suspect_declared_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS wanted_declared_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_game_complete BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS rank_victory_at TIMESTAMP WITH TIME ZONE")

    op.execute("ALTER TABLE ships ADD COLUMN IF NOT EXISTS shield_resistance DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ships ADD COLUMN IF NOT EXISTS armor_rating DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ship_specifications ADD COLUMN IF NOT EXISTS shield_resistance DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE ship_specifications ADD COLUMN IF NOT EXISTS armor_rating DOUBLE PRECISION NOT NULL DEFAULT 0")

    op.execute("ALTER TABLE fleets ADD COLUMN IF NOT EXISTS coordination_bonus DOUBLE PRECISION NOT NULL DEFAULT 0")

    # ------------------------------------------------------------------
    # 2. New tables (guarded by inspector — safe to re-run).
    # ------------------------------------------------------------------
    if not _has_table('sector_faction_influence'):
        op.create_table(
            'sector_faction_influence',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('sector_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('faction_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('influence_percentage', sa.Float(), nullable=False, server_default=sa.text('0')),
            sa.Column('patrol_spawn_weight', sa.Float(), nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['sector_id'], ['sectors.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['faction_id'], ['factions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('sector_id', 'faction_id', name='uq_sector_faction_influence'),
        )
        op.create_index('ix_sector_faction_influence_sector_id', 'sector_faction_influence', ['sector_id'])
        op.create_index('ix_sector_faction_influence_faction_id', 'sector_faction_influence', ['faction_id'])

    if not _has_table('medals'):
        op.create_table(
            'medals',
            sa.Column('id', sa.String(length=100), nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=50), nullable=False),
            sa.Column('tier', sa.String(length=50), nullable=True),
            sa.Column('criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_medals_category', 'medals', ['category'])

    if not _has_table('player_medals'):
        op.create_table(
            'player_medals',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('player_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('medal_id', sa.String(length=100), nullable=False),
            sa.Column('awarded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('awarded_via', sa.String(length=50), nullable=True),
            sa.Column('source_combat_log_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('source_event_key', sa.String(length=255), nullable=True),
            sa.Column('awarded_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('grant_batch_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('context_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('is_hidden_per_player', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
            # ON DELETE RESTRICT: block catalog deletes while held (ADR-0028).
            sa.ForeignKeyConstraint(['medal_id'], ['medals.id'], ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['source_combat_log_id'], ['combat_logs.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['awarded_by_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            # Idempotency keystone (ADR-0028).
            sa.UniqueConstraint('player_id', 'medal_id', name='uq_player_medal'),
        )
        op.create_index('ix_player_medals_player_id', 'player_medals', ['player_id'])
        op.create_index('ix_player_medals_medal_id', 'player_medals', ['medal_id'])
        op.create_index('ix_player_medals_player_awarded', 'player_medals', ['player_id', 'awarded_at'])

    if not _has_table('bounty_claims'):
        bounty_status_enum = postgresql.ENUM(
            *BOUNTY_CLAIM_STATUS_VALUES,
            name='bounty_claim_status',
            create_type=False,
        )
        # checkfirst makes the enum create idempotent across re-runs.
        bounty_status_enum.create(op.get_bind(), checkfirst=True)
        op.create_table(
            'bounty_claims',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('bounty_ref', sa.String(length=100), nullable=False),
            sa.Column('claimant_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('amount', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('status', bounty_status_enum, nullable=False, server_default=sa.text("'claimed'")),
            sa.Column('claimed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['claimant_id'], ['players.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['target_id'], ['players.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_bounty_claims_bounty_ref', 'bounty_claims', ['bounty_ref'])
        op.create_index('ix_bounty_claims_claimant_id', 'bounty_claims', ['claimant_id'])
        op.create_index('ix_bounty_claims_target_id', 'bounty_claims', ['target_id'])
        op.create_index('ix_bounty_claims_target_status', 'bounty_claims', ['target_id', 'status'])

    # ------------------------------------------------------------------
    # 3. faction_type enum: ADD VALUE IF NOT EXISTS (autocommit block).
    #    Must be last — autocommit_block() commits everything above first.
    #    ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    # ------------------------------------------------------------------
    with op.get_context().autocommit_block():
        for value in NEW_FACTION_TYPE_VALUES:
            op.execute(
                sa.text(f"ALTER TYPE factiontype ADD VALUE IF NOT EXISTS '{value}'")
            )


def downgrade() -> None:
    # New tables dropped (own data; reversible).
    if _has_table('bounty_claims'):
        op.drop_index('ix_bounty_claims_target_status', table_name='bounty_claims')
        op.drop_index('ix_bounty_claims_target_id', table_name='bounty_claims')
        op.drop_index('ix_bounty_claims_claimant_id', table_name='bounty_claims')
        op.drop_index('ix_bounty_claims_bounty_ref', table_name='bounty_claims')
        op.drop_table('bounty_claims')
        postgresql.ENUM(name='bounty_claim_status').drop(op.get_bind(), checkfirst=True)

    if _has_table('player_medals'):
        op.drop_index('ix_player_medals_player_awarded', table_name='player_medals')
        op.drop_index('ix_player_medals_medal_id', table_name='player_medals')
        op.drop_index('ix_player_medals_player_id', table_name='player_medals')
        op.drop_table('player_medals')

    if _has_table('medals'):
        op.drop_index('ix_medals_category', table_name='medals')
        op.drop_table('medals')

    if _has_table('sector_faction_influence'):
        op.drop_index('ix_sector_faction_influence_faction_id', table_name='sector_faction_influence')
        op.drop_index('ix_sector_faction_influence_sector_id', table_name='sector_faction_influence')
        op.drop_table('sector_faction_influence')

    # Additive columns dropped.
    op.execute("ALTER TABLE fleets DROP COLUMN IF EXISTS coordination_bonus")
    op.execute("ALTER TABLE ship_specifications DROP COLUMN IF EXISTS armor_rating")
    op.execute("ALTER TABLE ship_specifications DROP COLUMN IF EXISTS shield_resistance")
    op.execute("ALTER TABLE ships DROP COLUMN IF EXISTS armor_rating")
    op.execute("ALTER TABLE ships DROP COLUMN IF EXISTS shield_resistance")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS rank_victory_at")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS is_game_complete")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS wanted_declared_at")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS suspect_declared_at")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS is_wanted")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS is_suspect")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS max_turns")
    op.execute("ALTER TABLE players DROP COLUMN IF EXISTS last_turn_regeneration")

    # NOTE: Postgres cannot drop enum values. The four faction_type values
    # (Mining, Outlaws, Syndicate, Concord) REMAIN on the factiontype enum
    # after downgrade — same residue policy as the d4f7a2c91e58 precedent.
