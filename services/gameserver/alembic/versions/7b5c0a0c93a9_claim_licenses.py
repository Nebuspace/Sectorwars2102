"""claim licenses (mining) + MINING ship status

Mining slice (sw2102-docs FEATURES/economy/mining.md):

  - New ``claim_licenses`` table — an Astral Mining Consortium mining claim
    license held by a player for one (region, sector) pair. Grants
    penalty-free mining in AM-claimed ASTEROID_FIELD sectors for 24h.
    Unique on ``(player_id, region_id, sector_number)`` — one active license
    per (player, sector) at a time.

  - ``ship_status`` enum gains ``MINING`` — the momentary status a hull holds
    while harvesting an asteroid field (set + reset to IN_SPACE in the same
    synchronous harvest request).

The asteroid-richness / depletion_pool / has_deep_asteroids keys live on the
existing ``sectors.resources`` JSONB and are lazy-backfilled by the mining
service from ``sectors.resource_regeneration`` — they need NO migration.

ALTER TYPE ... ADD VALUE runs inside ``op.get_context().autocommit_block()``
(the b7e3a9d52c14 / d4f7a2c91e58 precedent): alembic commits the in-progress
migration transaction, executes the block with AUTOCOMMIT isolation, then
begins a fresh transaction. A direct mid-transaction isolation switch raises
InvalidRequestError on SQLAlchemy 2.x. The table DDL runs FIRST inside the
normal transaction; the only non-transactional work is the one enum append.
Idempotent via IF NOT EXISTS.

Additive only — no existing table is changed. Reversible: the downgrade drops
the table; the enum value remains (Postgres has no ALTER TYPE ... DROP VALUE,
the same residue as the b7e3a9d52c14 / d4f7a2c91e58 enum precedents), so any
MINING-status hull is parked back to IN_SPACE first.

Revision ID: 7b5c0a0c93a9
Revises: f1a4d7b2c9e3
Create Date: 2026-06-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '7b5c0a0c93a9'
down_revision = 'f1a4d7b2c9e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- claim_licenses table (additive, transactional) ---
    op.create_table(
        'claim_licenses',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('player_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('region_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('sector_number', sa.Integer(), nullable=False),
        sa.Column(
            'faction_code',
            sa.String(length=50),
            nullable=False,
            server_default='astral_mining_consortium',
        ),
        sa.Column('purchased_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('cost_paid_cr', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['player_id'], ['players.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['region_id'], ['regions.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'player_id', 'region_id', 'sector_number',
            name='uq_claim_license_player_region_sector',
        ),
    )
    # "Which licenses does this player hold?" — the harvest-time lookup.
    op.create_index(
        'ix_claim_licenses_player',
        'claim_licenses',
        ['player_id'],
        unique=False,
    )

    # --- ship_status enum: add MINING ---
    # autocommit_block() per the b7e3a9d52c14 / d4f7a2c91e58 precedent.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE ship_status ADD VALUE IF NOT EXISTS 'MINING'")
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE: MINING remains on the
    # ship_status enum after downgrade (same residue as the b7e3a9d52c14 /
    # d4f7a2c91e58 enum precedents). Park any mining hulls back in space so no
    # row references the value before it could become unmodeled.
    op.execute("UPDATE ships SET status = 'IN_SPACE' WHERE status = 'MINING'")

    op.drop_index('ix_claim_licenses_player', table_name='claim_licenses')
    op.drop_table('claim_licenses')
