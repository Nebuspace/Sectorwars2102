import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, DateTime, String, Integer, Float, ForeignKey, Boolean, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy import func, Index
import enum

from src.core.database import Base


class TransactionType(enum.Enum):
    BUY = "buy"
    SELL = "sell"
    TRANSFER = "transfer"
    ADMIN_ADJUSTMENT = "admin_adjustment"


class MarketTransaction(Base):
    __tablename__ = "enhanced_market_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Transaction participants. Exactly one of player_id / npc_id is set:
    # npc_id attributes TRADER-archetype NPC trades (SYSTEMS/
    # npc-lifecycle.md § Trade — NPCs are full market actors; attribution
    # field itself is canon-silent, flagged in DECISIONS.md).
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    npc_id = Column(UUID(as_uuid=True), ForeignKey("npc_characters.id", ondelete="SET NULL"), nullable=True)
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    
    # Transaction details
    transaction_type = Column(SQLEnum(TransactionType), nullable=False)
    commodity = Column(String(50), nullable=False)  # Food, Tech, Ore, Fuel
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer, nullable=False)  # price per unit in credits
    total_value = Column(Integer, nullable=False)  # quantity * unit_price
    
    # Market conditions at time of transaction
    station_buy_price = Column(Integer, nullable=True)  # station's buy price at time
    station_sell_price = Column(Integer, nullable=True)  # station's sell price at time
    station_quantity = Column(Integer, nullable=True)   # station's available quantity
    
    # Location and timing  
    sector_id = Column(Integer, nullable=True)  # Human-readable sector number
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Transaction metadata
    profit_margin = Column(Float, nullable=True)  # calculated profit percentage
    market_impact = Column(Float, nullable=True)  # price impact of this transaction

    # Tariff context at time of transaction (WO-TF — revenue analytics).
    # owner_tariff_rate is the EFFECTIVE region COMMERCE tariff in force when the
    # trade executed (the value compute_region_tariff_multiplier returned for the
    # station's region, already sliding-cap clamped). port_owner_id is the Player
    # who owned the station at trade time (NULL for unowned/NPC stations). These
    # RECORD who taxed and at what rate — they do NOT change the charge. Both are
    # nullable: pre-migration rows stay NULL, and a trade at an unowned station or
    # with a zero/failed tariff lookup records NULL/0.0 respectively.
    owner_tariff_rate = Column(Float, nullable=True)
    port_owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    
    # Admin fields
    admin_notes = Column(String(500), nullable=True)
    flagged_suspicious = Column(Boolean, nullable=False, default=False)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    # Relationships
    # Two FKs now point at players.id (player_id, port_owner_id), so both
    # player relationships must declare foreign_keys explicitly to disambiguate.
    player = relationship(
        "Player",
        foreign_keys=[player_id],
        back_populates="enhanced_market_transactions",
    )
    port_owner = relationship("Player", foreign_keys=[port_owner_id])
    station = relationship("Station")
    sector = relationship("Sector", foreign_keys=[sector_uuid])
    reviewer = relationship("User", foreign_keys=[reviewed_by])

    # Indexes for performance
    __table_args__ = (
        Index('ix_market_transactions_timestamp', 'timestamp'),
        Index('ix_market_transactions_commodity', 'commodity'),
        Index('ix_market_transactions_player_id', 'player_id'),
        Index('ix_market_transactions_station_id', 'station_id'),
        Index('ix_market_transactions_npc_id', 'npc_id'),
    )


class MarketPrice(Base):
    __tablename__ = "market_prices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Price tracking
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    commodity = Column(String(50), nullable=False)
    
    # Current prices
    buy_price = Column(Integer, nullable=False)   # what station pays players
    sell_price = Column(Integer, nullable=False)  # what station charges players
    quantity = Column(Integer, nullable=False, default=0)
    
    # Price history and volatility
    previous_buy_price = Column(Integer, nullable=True)
    previous_sell_price = Column(Integer, nullable=True)
    price_trend = Column(Float, nullable=False, default=0.0)  # positive = rising
    volatility = Column(Float, nullable=False, default=0.0)   # price volatility index
    
    # Market dynamics
    demand_level = Column(Float, nullable=False, default=1.0)  # demand multiplier
    supply_level = Column(Float, nullable=False, default=1.0)  # supply multiplier
    last_transaction_at = Column(DateTime(timezone=True), nullable=True)
    daily_volume = Column(Integer, nullable=False, default=0)
    
    # Price controls and alerts
    price_floor = Column(Integer, nullable=True)  # minimum price
    price_ceiling = Column(Integer, nullable=True)  # maximum price
    alert_threshold = Column(Float, nullable=True)  # alert if price changes by this %
    
    # Timestamps
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    station = relationship("Station")

    # Unique constraint and indexes
    __table_args__ = (
        Index('ix_market_prices_unique', 'station_id', 'commodity', unique=True),
        Index('ix_market_prices_updated_at', 'updated_at'),
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Price snapshot
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    commodity = Column(String(50), nullable=False)
    
    # Historical prices
    buy_price = Column(Integer, nullable=False)
    sell_price = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    
    # Market data
    daily_volume = Column(Integer, nullable=False, default=0)
    transactions_count = Column(Integer, nullable=False, default=0)
    average_transaction_size = Column(Float, nullable=False, default=0.0)
    
    # Economic indicators
    demand_level = Column(Float, nullable=False)
    supply_level = Column(Float, nullable=False)
    market_efficiency = Column(Float, nullable=False, default=1.0)
    
    # Snapshot metadata
    snapshot_date = Column(DateTime(timezone=True), nullable=False)
    snapshot_type = Column(String(20), nullable=False, default="daily")  # hourly, daily, weekly
    
    # Relationships
    station = relationship("Station")

    # Indexes for analytics queries
    __table_args__ = (
        Index('ix_price_history_date_commodity', 'snapshot_date', 'commodity'),
        Index('ix_price_history_port_date', 'station_id', 'snapshot_date'),
    )


class EconomicMetrics(Base):
    __tablename__ = "economic_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Metric period
    date = Column(DateTime(timezone=True), nullable=False, unique=True)
    metric_type = Column(String(20), nullable=False, default="daily")  # daily, weekly, monthly
    
    # Trade volume metrics
    total_trade_volume = Column(Integer, nullable=False, default=0)  # total credits traded
    total_transactions = Column(Integer, nullable=False, default=0)
    average_transaction_value = Column(Float, nullable=False, default=0.0)
    
    # Credit circulation
    total_credits_in_circulation = Column(Integer, nullable=False, default=0)
    credits_in_player_accounts = Column(Integer, nullable=False, default=0)
    credits_in_npc_accounts = Column(Integer, nullable=False, default=0)
    credit_velocity = Column(Float, nullable=False, default=0.0)  # how fast credits move
    
    # Market health indicators
    economic_health_score = Column(Float, nullable=False, default=0.5)  # 0-1 scale
    inflation_rate = Column(Float, nullable=False, default=0.0)
    average_profit_margin = Column(Float, nullable=False, default=0.0)
    market_volatility = Column(Float, nullable=False, default=0.0)
    
    # Commodity statistics
    most_traded_commodity = Column(String(50), nullable=True)
    least_traded_commodity = Column(String(50), nullable=True)
    commodity_price_index = Column(Float, nullable=False, default=100.0)  # base index
    
    # Regional economic data
    most_active_sector = Column(Integer, nullable=True)
    most_valuable_station = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    economic_disparity_index = Column(Float, nullable=False, default=0.0)  # wealth inequality
    
    # Player economics
    richest_player_credits = Column(Integer, nullable=False, default=0)
    median_player_credits = Column(Integer, nullable=False, default=0)
    total_players_trading = Column(Integer, nullable=False, default=0)
    new_traders = Column(Integer, nullable=False, default=0)
    
    # Calculated metrics
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    most_valuable_station_ref = relationship("Station", foreign_keys=[most_valuable_station])

    # Indexes
    __table_args__ = (
        Index('ix_economic_metrics_date', 'date'),
        Index('ix_economic_metrics_type_date', 'metric_type', 'date'),
    )


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Alert configuration
    station_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    commodity = Column(String(50), nullable=False)
    
    # Alert conditions
    alert_type = Column(String(30), nullable=False)  # price_spike, price_drop, high_volume, low_supply
    threshold_value = Column(Float, nullable=False)
    current_value = Column(Float, nullable=False)
    
    # Alert details
    severity = Column(String(20), nullable=False, default="medium")  # low, medium, high, critical
    message = Column(String(500), nullable=False)
    suggested_action = Column(String(200), nullable=True)
    
    # Alert status
    is_active = Column(Boolean, nullable=False, default=True)
    triggered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acknowledged_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Auto-resolve conditions
    auto_resolve = Column(Boolean, nullable=False, default=True)
    resolve_threshold = Column(Float, nullable=True)
    
    # Relationships
    station = relationship("Station")
    acknowledger = relationship("User", foreign_keys=[acknowledged_by])

    # Indexes
    __table_args__ = (
        Index('ix_price_alerts_active', 'is_active'),
        Index('ix_price_alerts_triggered', 'triggered_at'),
    )


# Add economic relationships to existing models
# This would be added to the Player model:
# market_transactions = relationship("MarketTransaction", back_populates="player")

# This would be added to the Station model:
# market_prices = relationship("MarketPrice", back_populates="port")
# price_history = relationship("PriceHistory", back_populates="port")
# price_alerts = relationship("PriceAlert", back_populates="port")
# market_transactions = relationship("MarketTransaction", back_populates="port")