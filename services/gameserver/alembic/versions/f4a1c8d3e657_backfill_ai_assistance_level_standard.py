"""Backfill player_trading_profiles.ai_assistance_level 'medium' -> 'standard'.

4-level ARIA assistance vocab ratified 2026-08-04 (human, per ADR-0068):
minimal/quiet/standard/full, replacing the 3-value minimal/medium/full.
'medium' is renamed to 'standard' with identical semantics -- this is a
data-only, WHERE-guarded, idempotent backfill (no DDL), mirroring the
house convention (see b601fcdaca25_backfill_station_security_tier.py).
Reversible: downgrade re-labels 'standard' rows back to 'medium'.

Revision ID: f4a1c8d3e657
Revises: a8c3e1f92b47
Create Date: 2026-08-04 21:20:00.000000

"""
from alembic import op


revision = "f4a1c8d3e657"
down_revision = "a8c3e1f92b47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE player_trading_profiles SET ai_assistance_level = 'standard' "
        "WHERE ai_assistance_level = 'medium'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE player_trading_profiles SET ai_assistance_level = 'medium' "
        "WHERE ai_assistance_level = 'standard'"
    )
