"""rename_all_port_columns_to_station

Revision ID: e86cb8130b5b
Revises: c138b33baec4
Create Date: 2025-11-16 22:45:28.836334

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e86cb8130b5b'
down_revision = 'c138b33baec4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename player state column
    op.alter_column('players', 'is_ported', new_column_name='is_docked')

    # Rename market transaction columns
    op.alter_column('enhanced_market_transactions', 'port_id', new_column_name='station_id')
    op.alter_column('enhanced_market_transactions', 'port_buy_price', new_column_name='station_buy_price')
    op.alter_column('enhanced_market_transactions', 'port_sell_price', new_column_name='station_sell_price')
    op.alter_column('enhanced_market_transactions', 'port_quantity', new_column_name='station_quantity')

    # Rename market price columns
    op.alter_column('market_prices', 'port_id', new_column_name='station_id')

    # Rename price history columns
    op.alter_column('price_history', 'port_id', new_column_name='station_id')

    # Rename economic metrics columns
    op.alter_column('economic_metrics', 'most_valuable_port', new_column_name='most_valuable_station')

    # Rename price alert columns
    op.alter_column('price_alerts', 'port_id', new_column_name='station_id')

    # Rename markets table column
    op.alter_column('markets', 'port_id', new_column_name='station_id')

    # Rename indexes
    op.drop_index('ix_market_transactions_port_id', table_name='enhanced_market_transactions')
    op.create_index('ix_market_transactions_station_id', 'enhanced_market_transactions', ['station_id'])

    op.drop_index('ix_price_history_port_date', table_name='price_history')
    op.create_index('ix_price_history_station_date', 'price_history', ['station_id', 'snapshot_date'])

    # Recreate unique constraint on market_prices with new column name
    op.drop_index('ix_market_prices_unique', table_name='market_prices')
    op.create_index('ix_market_prices_unique', 'market_prices', ['station_id', 'commodity'], unique=True)


def downgrade() -> None:
    # Revert indexes
    op.drop_index('ix_market_prices_unique', table_name='market_prices')
    op.create_index('ix_market_prices_unique', 'market_prices', ['port_id', 'commodity'], unique=True)

    op.drop_index('ix_price_history_station_date', table_name='price_history')
    op.create_index('ix_price_history_port_date', 'price_history', ['port_id', 'snapshot_date'])

    op.drop_index('ix_market_transactions_station_id', table_name='enhanced_market_transactions')
    op.create_index('ix_market_transactions_port_id', 'enhanced_market_transactions', ['port_id'])

    # Revert column renames
    op.alter_column('markets', 'station_id', new_column_name='port_id')
    op.alter_column('price_alerts', 'station_id', new_column_name='port_id')
    op.alter_column('economic_metrics', 'most_valuable_station', new_column_name='most_valuable_port')
    op.alter_column('price_history', 'station_id', new_column_name='port_id')
    op.alter_column('market_prices', 'station_id', new_column_name='port_id')
    op.alter_column('enhanced_market_transactions', 'station_quantity', new_column_name='port_quantity')
    op.alter_column('enhanced_market_transactions', 'station_sell_price', new_column_name='port_sell_price')
    op.alter_column('enhanced_market_transactions', 'station_buy_price', new_column_name='port_buy_price')
    op.alter_column('enhanced_market_transactions', 'station_id', new_column_name='port_id')
    op.alter_column('players', 'is_docked', new_column_name='is_ported')