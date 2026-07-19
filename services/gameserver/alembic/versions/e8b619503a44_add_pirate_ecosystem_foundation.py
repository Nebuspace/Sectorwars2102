"""Add pirate ecosystem foundation -- PirateHolding, PirateKillLog, Region.pirate_ecosystem_state (WO-PIRATE-ECO-1)

Per ADR-0047/0048 (Accepted): the pirate ecosystem is entirely unbuilt at
HEAD. This is the foundation slice -- two new tables plus one additive nullable
JSONB column on the existing `regions` table. No backfill (new tables start
empty; the JSONB column defaults NULL and is lazy-initialized by
pirate_ecosystem_service on first read). Fully additive and reversible.

See src/models/pirate_holding.py and src/models/pirate_kill_log.py module
docstrings for the documented divergences from the canon column shape
(PirateHolding.sector_id GLOBAL-integer convention; PirateKillLog.
attacker_player_id nullable). PirateKillLog itself is otherwise canon-exact:
region_id NOT NULL, no sector_id column (every row is a holding-CLEAR event
carrying a holding, per pirate-ecosystem.md:95-96 verbatim -- sector, when
needed, resolves via holding_id -> PirateHolding.sector_id).

Revision ID: e8b619503a44
Revises: eb772a1ab433
Create Date: 2026-07-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e8b619503a44'
down_revision = 'eb772a1ab433'
branch_labels = None
depends_on = None


PIRATE_HOLDING_TIER_VALUES = ('CAMP', 'OUTPOST', 'STRONGHOLD')
PIRATE_KILL_DISPOSITION_VALUES = ('CAPTURED', 'CLEARED')


def upgrade() -> None:
    pirate_holding_tier = postgresql.ENUM(
        *PIRATE_HOLDING_TIER_VALUES, name='pirate_holding_tier'
    )
    pirate_kill_disposition = postgresql.ENUM(
        *PIRATE_KILL_DISPOSITION_VALUES, name='pirate_kill_disposition'
    )
    bind = op.get_bind()
    pirate_holding_tier.create(bind, checkfirst=True)
    pirate_kill_disposition.create(bind, checkfirst=True)

    op.create_table(
        'pirate_holdings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('region_id', postgresql.UUID(as_uuid=True), nullable=False),
        # GLOBAL sectors.sector_id (Station.sector_id / NPCCharacter.current_sector_id
        # convention) -- NOT a UUID FK to sectors.id.
        sa.Column('sector_id', sa.Integer(), nullable=False),
        sa.Column(
            'tier',
            postgresql.ENUM(*PIRATE_HOLDING_TIER_VALUES, name='pirate_holding_tier', create_type=False),
            nullable=False,
        ),
        # Non-NULL = player-captured.
        sa.Column('owner_player_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('current_strength', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('last_damage_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_player_id'], ['players.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            'current_strength >= 0.0 AND current_strength <= 1.0',
            name='valid_pirate_holding_current_strength',
        ),
    )
    op.create_index(
        'ix_pirate_holdings_region_id', 'pirate_holdings', ['region_id']
    )
    op.create_index(
        'ix_pirate_holdings_sector_id', 'pirate_holdings', ['sector_id']
    )
    op.create_index(
        'ix_pirate_holdings_owner_player_id', 'pirate_holdings', ['owner_player_id']
    )
    op.create_index(
        'ix_pirate_holdings_region_owner', 'pirate_holdings', ['region_id', 'owner_player_id']
    )

    op.create_table(
        'pirate_kill_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        # Canon-exact NOT NULL -- every row is a holding-CLEAR event; see
        # pirate_kill_log.py module docstring.
        sa.Column('region_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('holding_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            'tier',
            postgresql.ENUM(*PIRATE_HOLDING_TIER_VALUES, name='pirate_holding_tier', create_type=False),
            nullable=False,
        ),
        sa.Column('kill_weight', sa.Integer(), nullable=False),
        # NULLABLE -- see module docstring divergence note.
        sa.Column('attacker_player_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('attacker_team_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            'disposition',
            postgresql.ENUM(*PIRATE_KILL_DISPOSITION_VALUES, name='pirate_kill_disposition', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['holding_id'], ['pirate_holdings.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['attacker_player_id'], ['players.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['attacker_team_id'], ['teams.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pirate_kill_log_region_id', 'pirate_kill_log', ['region_id'])
    op.create_index(
        'ix_pirate_kill_log_region_created', 'pirate_kill_log', ['region_id', 'created_at']
    )

    # Additive nullable JSONB column on the existing regions table (pirate-
    # ecosystem.md:379-399). No backfill -- pre-migration rows read NULL;
    # pirate_ecosystem_service lazy-initializes on first refresh.
    op.add_column(
        'regions', sa.Column('pirate_ecosystem_state', postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('regions', 'pirate_ecosystem_state')

    op.drop_index('ix_pirate_kill_log_region_created', table_name='pirate_kill_log')
    op.drop_index('ix_pirate_kill_log_region_id', table_name='pirate_kill_log')
    op.drop_table('pirate_kill_log')

    op.drop_index('ix_pirate_holdings_region_owner', table_name='pirate_holdings')
    op.drop_index('ix_pirate_holdings_owner_player_id', table_name='pirate_holdings')
    op.drop_index('ix_pirate_holdings_sector_id', table_name='pirate_holdings')
    op.drop_index('ix_pirate_holdings_region_id', table_name='pirate_holdings')
    op.drop_table('pirate_holdings')

    bind = op.get_bind()
    postgresql.ENUM(*PIRATE_KILL_DISPOSITION_VALUES, name='pirate_kill_disposition').drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*PIRATE_HOLDING_TIER_VALUES, name='pirate_holding_tier').drop(
        bind, checkfirst=True
    )
