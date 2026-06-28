"""add grey-flag PvP status to players (grey_until + grey_kind)

WO-BL — Grey-flag PvP status. Adds the storage for the "grey" temporary
penalty-free-to-attack status (Max-ruled design):

  - Attacking a GOOD-STANDING player → the attacker goes GREY for 1 HOUR.
    While grey, GOOD-STANDING players may attack the grey player with NO
    reputation penalty.
  - Attacking a STATION → the attacker goes GREY for 1 DAY. While grey, ANY
    player (good or evil) may attack the grey player penalty-free.
  - Grey auto-expires at ``grey_until`` OR is cleared early by paying a fine.

Two new **nullable** columns on ``players``:

  - ``grey_until`` — DateTime(timezone=True), nullable. The UTC timestamp at
    which the grey status expires. NULL = not grey (the default). A longer
    remaining grey is never extended DOWNWARD by a lesser later offense — the
    grey service takes MAX(existing, new).

  - ``grey_kind`` — String(20), nullable. The offense class that set the grey
    flag, needed so the penalty-free predicate can distinguish:
        * ``player_attack`` → only GOOD-STANDING attackers are penalty-free.
        * ``station_attack`` → ANY attacker is penalty-free.
    NULL when not grey. Cached at set time alongside grey_until.

Purely **ADDITIVE / non-destructive**: two brand-new nullable columns with no
server_default and no backfill. Existing ``players`` rows are valid immediately
(grey_until NULL = not grey). The grey service sets/clears these columns only on
a live offense / fine-clear / auto-expiry; nothing else touches them.

This does NOT remove or alter the existing is_suspect / is_wanted columns — WO-BL
keeps those columns in place (the combat auto-set was already dead code, so the
combat path simply never sets them). The canon-correct cargo-wreck / stolen-ship
suspect/wanted triggers (when they land) remain free to use those columns.

Chained onto the current verified dev head ``b7e1d4f92a38`` (Ship.tow_state,
WO-AF). Does NOT branch.

Revision ID: c8f2a1d6e9b4
Revises: b7e1d4f92a38
Create Date: 2026-06-20 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f2a1d6e9b4'
down_revision = 'b7e1d4f92a38'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive nullable columns. No server_default: an absent grey status is
    # NULL, not a sentinel — grey is set by the grey-flag service only on a live
    # offense, and cleared (set back to NULL) on fine-clear / auto-expiry.
    op.add_column(
        'players',
        sa.Column('grey_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'players',
        sa.Column('grey_kind', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('players', 'grey_kind')
    op.drop_column('players', 'grey_until')
