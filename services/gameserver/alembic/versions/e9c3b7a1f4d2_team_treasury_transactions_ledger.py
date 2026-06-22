"""team treasury transactions ledger (TT)

Creates the new ``team_treasury_transactions`` table — an append-only ledger of
every Team-treasury-affecting event, written one row per mutation in the same
transaction as the balance change by ``TeamService`` (deposit / withdraw /
transfer-to-player). Mirrors the ``regional_treasury_entries`` ledger shape
(before/after auditable balance) so a team's treasury history is reconstructable
and renderable newest-first.

Additive + reversible: a brand-new table, no backfill, no change to any existing
table. The downgrade drops the table cleanly.

Single-head chaining: at author time the branch ``feat/living-npc-system`` had
exactly ONE alembic head — ``d7a2f1c9e3b5`` (planet abandonment / reclaim +
inert tax_rate). This migration chains strictly onto that head so it does NOT
create a spurious independent head.

Revision ID: e9c3b7a1f4d2
Revises: d7a2f1c9e3b5
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e9c3b7a1f4d2'
down_revision = 'd7a2f1c9e3b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'team_treasury_transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('actor_player_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_player_id'], ['players.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Newest-first history for one team is the hot path.
    op.create_index(
        'ix_team_treasury_tx_team_created',
        'team_treasury_transactions',
        ['team_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_team_treasury_tx_team_created', table_name='team_treasury_transactions')
    op.drop_table('team_treasury_transactions')
