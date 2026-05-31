"""bang_schema_decisions: Q1/Q2/Q6 column + enum changes

Phase 1B schema decisions resolved 2026-05-31 (see
DOCS/PLANS/bang-integration-schema-map.md § 6):

  Q1 - 9th commodity is ``precious_metals`` (ADR-0062 E-D1, band 80-180).
       This migration extends the ``stations.commodities`` JSONB default
       to include the new block (any rows already inserted are NOT
       backfilled here — runtime code will lazy-fill on first market
       update if the key is absent).
       Note: ``COMMODITY_PRICE_RANGES`` lives in
       ``src/services/trading_service.py`` as a Python dict, not as a
       database table; no DB row needs adding for the price band.
       (Verified by grepping the alembic versions tree: no
       commodity_price_ranges table found, skipping ADR-0062 E-D1
       sub-step.)

  Q2 - Add ``stations.is_spacedock BOOLEAN NOT NULL DEFAULT false``.

  Q6 - Extend Postgres enum ``special_formation_type`` with the 3 ADR-0070
       island values: LOST_SECTOR, LOST_CLUSTER, ARCHIPELAGO.
       Postgres requires ALTER TYPE ... ADD VALUE to run outside any
       wrapping transaction (each ADD VALUE auto-commits). Alembic's
       default migration runs inside a transaction, so we set
       ``with_variant``/``transaction_per_migration`` via the env config
       OR (as here) issue each ALTER TYPE as a separate
       ``op.execute`` call against ``connection.execution_options(
       isolation_level='AUTOCOMMIT')``.

Revision ID: c4f1d8b27e63
Revises: b3e5c7a92f48
Create Date: 2026-05-31 00:00:02.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4f1d8b27e63'
down_revision = 'b3e5c7a92f48'
branch_labels = None
depends_on = None


# New enum values to append (in order).
NEW_FORMATION_VALUES = ('LOST_SECTOR', 'LOST_CLUSTER', 'ARCHIPELAGO')


def upgrade() -> None:
    # --- Q2: Station.is_spacedock ---
    op.add_column(
        'stations',
        sa.Column(
            'is_spacedock',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # --- Q1: extend the commodities JSONB default ---
    # We only update the column default. Existing rows keep their
    # 8-commodity dict; runtime code (or a later data-backfill task) is
    # responsible for adding the precious_metals key to existing stations.
    # Doing the backfill here would require a JSONB UPDATE across every
    # station row — out of scope for the schema migration; if needed it
    # will land as a separate data migration.
    new_default = sa.text("""
        '{
            "ore": {"quantity": 1000, "capacity": 5000, "base_price": 15, "current_price": 15, "production_rate": 100, "price_variance": 20, "buys": false, "sells": true},
            "organics": {"quantity": 800, "capacity": 3000, "base_price": 18, "current_price": 18, "production_rate": 80, "price_variance": 25, "buys": true, "sells": false},
            "equipment": {"quantity": 500, "capacity": 2000, "base_price": 35, "current_price": 35, "production_rate": 50, "price_variance": 30, "buys": true, "sells": true},
            "fuel": {"quantity": 1500, "capacity": 4000, "base_price": 12, "current_price": 12, "production_rate": 120, "price_variance": 15, "buys": false, "sells": true},
            "luxury_goods": {"quantity": 200, "capacity": 800, "base_price": 100, "current_price": 100, "production_rate": 20, "price_variance": 40, "buys": false, "sells": false},
            "gourmet_food": {"quantity": 150, "capacity": 600, "base_price": 80, "current_price": 80, "production_rate": 15, "price_variance": 35, "buys": false, "sells": false},
            "exotic_technology": {"quantity": 50, "capacity": 200, "base_price": 250, "current_price": 250, "production_rate": 5, "price_variance": 50, "buys": false, "sells": false},
            "colonists": {"quantity": 100, "capacity": 500, "base_price": 50, "current_price": 50, "production_rate": 10, "price_variance": 10, "buys": false, "sells": false},
            "precious_metals": {"quantity": 80, "capacity": 400, "base_price": 130, "current_price": 130, "production_rate": 8, "price_variance": 30, "buys": false, "sells": false}
        }'::jsonb
    """)
    op.alter_column(
        'stations',
        'commodities',
        server_default=new_default,
    )

    # --- Q6: extend special_formation_type enum ---
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction,
    # but only when the new value is used in the SAME transaction. To
    # avoid that footgun and stay compatible with older PG versions, run
    # each ADD VALUE with AUTOCOMMIT isolation. They are idempotent via
    # IF NOT EXISTS.
    bind = op.get_bind()
    autocommit_conn = bind.execution_options(isolation_level='AUTOCOMMIT')
    for value in NEW_FORMATION_VALUES:
        autocommit_conn.execute(
            sa.text(
                f"ALTER TYPE special_formation_type ADD VALUE IF NOT EXISTS '{value}'"
            )
        )


def downgrade() -> None:
    # --- Q1: revert commodities default to 8-commodity dict ---
    old_default = sa.text("""
        '{
            "ore": {"quantity": 1000, "capacity": 5000, "base_price": 15, "current_price": 15, "production_rate": 100, "price_variance": 20, "buys": false, "sells": true},
            "organics": {"quantity": 800, "capacity": 3000, "base_price": 18, "current_price": 18, "production_rate": 80, "price_variance": 25, "buys": true, "sells": false},
            "equipment": {"quantity": 500, "capacity": 2000, "base_price": 35, "current_price": 35, "production_rate": 50, "price_variance": 30, "buys": true, "sells": true},
            "fuel": {"quantity": 1500, "capacity": 4000, "base_price": 12, "current_price": 12, "production_rate": 120, "price_variance": 15, "buys": false, "sells": true},
            "luxury_goods": {"quantity": 200, "capacity": 800, "base_price": 100, "current_price": 100, "production_rate": 20, "price_variance": 40, "buys": false, "sells": false},
            "gourmet_food": {"quantity": 150, "capacity": 600, "base_price": 80, "current_price": 80, "production_rate": 15, "price_variance": 35, "buys": false, "sells": false},
            "exotic_technology": {"quantity": 50, "capacity": 200, "base_price": 250, "current_price": 250, "production_rate": 5, "price_variance": 50, "buys": false, "sells": false},
            "colonists": {"quantity": 100, "capacity": 500, "base_price": 50, "current_price": 50, "production_rate": 10, "price_variance": 10, "buys": false, "sells": false}
        }'::jsonb
    """)
    op.alter_column(
        'stations',
        'commodities',
        server_default=old_default,
    )

    # --- Q2: drop is_spacedock ---
    op.drop_column('stations', 'is_spacedock')

    # --- Q6: Postgres has no DROP VALUE on an enum. The 3 added values
    # remain on downgrade. If a clean rollback is required, drop the
    # whole type + recreate from the original 9-value set; that requires
    # destructively migrating any special_formations rows that already
    # use the new values. Left as a manual operation. ---
