"""drop vestigial faction_missions table (WO-FM / DECISION #3)

The ``faction_missions`` table was created by the initial-schema migration
(``c138b33baec4``) to back a faction-mission feature that never reached the
player. The whole surface is vestigial and contradicts ADR-0090 (missions no
longer promise reputation): the player-facing list/accept endpoints, the admin
create/list endpoints, the ``FactionService`` mission helpers, and the
``FactionMission`` model + ``Faction.missions`` relationship have all been
removed in WO-FM. This migration drops the now-orphaned table.

Verification before drop (WO-FM):
  - NO Python model maps to ``faction_missions`` after this change (the only
    model, ``FactionMission``, is deleted in the same WO).
  - NO foreign key anywhere references ``faction_missions.id`` (its own single
    FK points OUTWARD at ``factions.id`` only).
  - The table holds 1 vestigial row, which this drop removes (Max-blessed
    destructive drop per DECISION #3).

The drop is destructive but reversible: ``downgrade()`` recreates the table
exactly per its current ``FactionMission`` model definition (mirroring the
initial-schema migration ``c138b33baec4``). The table uses no enum types, so
there is nothing to recreate with ``create_type=False``.

Revision ID: 150381baa0c5
Revises: f2a9c4e7b1d8
Create Date: 2026-06-22 22:29:20.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '150381baa0c5'
down_revision = 'f2a9c4e7b1d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the vestigial faction-mission table. The model + all routes/service
    # helpers that touched it are removed in the same WO, and no FK references
    # it, so the drop is self-contained. The single vestigial row is removed
    # with the table (Max-blessed per DECISION #3).
    op.drop_index(op.f('ix_faction_missions_faction_id'), table_name='faction_missions')
    op.drop_table('faction_missions')


def downgrade() -> None:
    # Recreate the table exactly as defined in the initial-schema migration
    # c138b33baec4 / the FactionMission model (fully reversible structure).
    op.create_table(
        'faction_missions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('faction_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('mission_type', sa.String(length=50), nullable=False),
        sa.Column('min_reputation', sa.Integer(), nullable=True),
        sa.Column('min_level', sa.Integer(), nullable=True),
        sa.Column('credit_reward', sa.Integer(), nullable=True),
        sa.Column('reputation_reward', sa.Integer(), nullable=True),
        sa.Column('item_rewards', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('target_sector_id', sa.UUID(), nullable=True),
        sa.Column('cargo_type', sa.String(length=50), nullable=True),
        sa.Column('cargo_quantity', sa.Integer(), nullable=True),
        sa.Column('target_faction_id', sa.UUID(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['faction_id'], ['factions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_faction_missions_faction_id'),
        'faction_missions',
        ['faction_id'],
        unique=False,
    )
