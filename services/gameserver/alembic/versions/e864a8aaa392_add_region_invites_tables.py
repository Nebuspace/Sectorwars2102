"""add region_invites + region_invite_redemptions tables (WO-IL1)

Invite-link region onramp — design brief
audit/design-briefs/invite-link-onramp.md §3 (DECISIONS.md:473-475).

Creates TWO brand-new tables for the auth-free invite infrastructure:

  - ``region_invites`` — the region-owner-minted, expiring, revocable redeem
    key. ``code`` is UNIQUE + indexed (the redeem lookup key); ``region_id``
    is FK regions.id ON DELETE CASCADE + indexed (so a hard-deleted region
    removes its outstanding invites); ``created_by`` is FK users.id ON DELETE
    SET NULL (provenance survives owner deletion). ``status`` is a plain
    String(20) enum-in-string (active | exhausted | revoked | expired) — no
    native PG enum, so adding a status value later needs no migration.
    ``expires_at`` is NOT NULL with NO server_default (mandatory TTL, supplied
    by the minting service). Two CHECK constraints enforce the status
    vocabulary and ``uses >= 0 AND max_uses >= 1 AND uses <= max_uses`` so the
    exhaustion gate can never be bypassed by a bad write.

  - ``region_invite_redemptions`` — append-only audit trail. ``invite_id`` FK
    region_invites.id ON DELETE CASCADE + indexed; ``redeemed_by_player_id`` FK
    players.id ON DELETE SET NULL (nullable — set after the player row is
    created in the same redeem transaction). ``ip_hash`` /
    ``device_fingerprint_hash`` are nullable hashed columns (never raw),
    feeding the future ADR-0056 multi-account clustering.

Purely **ADDITIVE / forward-only**: two new tables, no change to any existing
table or row. No backfill. Chained onto the verified linear dev head
``c8f2a1d6e9b4`` (WO-BL grey-flag). Does NOT branch. Downgrade drops both
tables (indexes first) and leaves the rest of the schema untouched.

Revision ID: e864a8aaa392
Revises: c8f2a1d6e9b4
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e864a8aaa392'
down_revision = 'c8f2a1d6e9b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- region_invites ---
    op.create_table(
        'region_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column(
            'region_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('regions.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'created_by',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('uses', sa.Integer(), nullable=False, server_default=sa.text('0')),
        # Mandatory TTL — NO server_default; supplied by the minting service.
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'exhausted', 'revoked', 'expired')",
            name='valid_region_invite_status',
        ),
        sa.CheckConstraint(
            'uses >= 0 AND max_uses >= 1 AND uses <= max_uses',
            name='valid_region_invite_uses',
        ),
    )
    op.create_index(
        'ix_region_invites_code', 'region_invites', ['code'], unique=True
    )
    op.create_index(
        'ix_region_invites_region_id', 'region_invites', ['region_id']
    )

    # --- region_invite_redemptions ---
    op.create_table(
        'region_invite_redemptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'invite_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('region_invites.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'redeemed_by_player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'redeemed_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('ip_hash', sa.String(), nullable=True),
        sa.Column('device_fingerprint_hash', sa.String(), nullable=True),
    )
    op.create_index(
        'ix_region_invite_redemptions_invite_id',
        'region_invite_redemptions',
        ['invite_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_region_invite_redemptions_invite_id',
        table_name='region_invite_redemptions',
    )
    op.drop_table('region_invite_redemptions')

    op.drop_index('ix_region_invites_region_id', table_name='region_invites')
    op.drop_index('ix_region_invites_code', table_name='region_invites')
    op.drop_table('region_invites')
