"""player formation knowledge (ADR-0045 -- per-player special-formation discovery)

WO-GWQ-FORMATION-KNOWLEDGE -- replaces ``SpecialFormation.is_discovered`` as
the ONLY discovery gate (a global Boolean any player's visit flips for
everyone) with a per-player ``player_formation_knowledge`` table, closing the
cross-player identity leak. Mirrors ``player_warp_knowledge`` (same
ADR-0045, WO-LW): one row per (player_id, formation_id) records that THIS
player has personally discovered the formation. ``is_discovered`` remains on
``special_formations`` as a global aggregate (first-ever-discovery flag +
one-time name back-fill trigger) -- unchanged, still written.

Additive + reversible: one new table + one new enum type
(formation_revealed_via), no backfill, no change to any existing table or
column. Downgrade drops the table and its enum cleanly.

Revision ID: fea17cc334a8
Revises: a3f9e1c74b28
Create Date: 2026-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'fea17cc334a8'
down_revision = 'a3f9e1c74b28'
branch_labels = None
depends_on = None


FORMATION_REVEALED_VIA = ('VISIT',)


def upgrade() -> None:
    formation_revealed_via = postgresql.ENUM(
        *FORMATION_REVEALED_VIA, name='formation_revealed_via'
    )
    bind = op.get_bind()
    formation_revealed_via.create(bind, checkfirst=True)

    op.create_table(
        'player_formation_knowledge',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('player_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('formation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'revealed_via',
            postgresql.ENUM(
                *FORMATION_REVEALED_VIA, name='formation_revealed_via', create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            'discovered_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['formation_id'], ['special_formations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'player_id', 'formation_id',
            name='uq_player_formation_knowledge_player_formation',
        ),
    )
    # "Which formations does this player know?" -- the per-player map read.
    op.create_index(
        'ix_player_formation_knowledge_player',
        'player_formation_knowledge',
        ['player_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_player_formation_knowledge_player', table_name='player_formation_knowledge'
    )
    op.drop_table('player_formation_knowledge')

    bind = op.get_bind()
    postgresql.ENUM(*FORMATION_REVEALED_VIA, name='formation_revealed_via').drop(
        bind, checkfirst=True
    )
