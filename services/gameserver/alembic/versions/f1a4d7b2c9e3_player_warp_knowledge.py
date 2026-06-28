"""player warp knowledge (LW — per-player latent-warp discovery)

Creates the ``player_warp_knowledge`` table per ADR-0045 (per-player ARIA-driven
warp knowledge; canonical rule in FEATURES/gameplay/aria-companion.md § Warp
discovery). One row per ``(player_id, warp_layer, warp_id)`` records which warps
a player personally knows about — a latent warp stays invisible to a player until
they hold a ``revealed`` or ``traversed`` row for it. Knowledge is per-player; one
player's discovery never leaks the warp to rivals.

Additive + reversible: a brand-new table and three new enum types, no backfill, no
change to any existing table. The downgrade drops the table and its enums cleanly.

Single-head chaining: at author time the branch ``feat/living-npc-system`` had
exactly ONE alembic head — ``e9c3b7a1f4d2`` (team treasury transactions ledger).
This migration chains strictly onto that head so it does NOT create a spurious
independent head. (The lead may re-chain TF/LW linearly at integration.)

Revision ID: f1a4d7b2c9e3
Revises: e9c3b7a1f4d2
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f1a4d7b2c9e3'
# Re-chained linearly onto TF (was e9c3b7a1f4d2) at integration so TT->TF->LW is a
# single linear head, not a 2-way fork off e9c3b7a1f4d2 (lead instruction).
down_revision = 'f1a4d8c2e7b9'
branch_labels = None
depends_on = None


# Enum value sets (must match src/models/player_warp_knowledge.py).
WARP_LAYER = ('sector_warps', 'warp_tunnels')
WARP_VISIBILITY_STATE = ('hidden', 'revealed', 'traversed')
WARP_REVEALED_VIA = ('scan', 'traversal_attempt', 'corp_share', 'aria_inference')


def upgrade() -> None:
    warp_layer = postgresql.ENUM(*WARP_LAYER, name='warp_layer')
    warp_visibility_state = postgresql.ENUM(
        *WARP_VISIBILITY_STATE, name='warp_visibility_state'
    )
    warp_revealed_via = postgresql.ENUM(
        *WARP_REVEALED_VIA, name='warp_revealed_via'
    )
    bind = op.get_bind()
    warp_layer.create(bind, checkfirst=True)
    warp_visibility_state.create(bind, checkfirst=True)
    warp_revealed_via.create(bind, checkfirst=True)

    op.create_table(
        'player_warp_knowledge',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('player_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'warp_layer',
            postgresql.ENUM(*WARP_LAYER, name='warp_layer', create_type=False),
            nullable=False,
        ),
        sa.Column('warp_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'visibility_state',
            postgresql.ENUM(
                *WARP_VISIBILITY_STATE,
                name='warp_visibility_state',
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'revealed_via',
            postgresql.ENUM(
                *WARP_REVEALED_VIA, name='warp_revealed_via', create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            'discovered_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'last_updated',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'player_id', 'warp_layer', 'warp_id',
            name='uq_player_warp_knowledge_player_layer_warp',
        ),
    )
    # "Which warps does this player know?" — the per-player map read.
    op.create_index(
        'ix_player_warp_knowledge_player',
        'player_warp_knowledge',
        ['player_id'],
        unique=False,
    )
    # "Who knows about this warp?" — corp-share propagation / admin.
    op.create_index(
        'ix_player_warp_knowledge_warp',
        'player_warp_knowledge',
        ['warp_layer', 'warp_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_player_warp_knowledge_warp', table_name='player_warp_knowledge'
    )
    op.drop_index(
        'ix_player_warp_knowledge_player', table_name='player_warp_knowledge'
    )
    op.drop_table('player_warp_knowledge')

    bind = op.get_bind()
    postgresql.ENUM(*WARP_REVEALED_VIA, name='warp_revealed_via').drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*WARP_VISIBILITY_STATE, name='warp_visibility_state').drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*WARP_LAYER, name='warp_layer').drop(bind, checkfirst=True)
