"""Player-to-player trade window models (ADR-0089 / WO-P2P-TRADING-SYSTEM).

v1 kernel: credits + commodities only. Ship-bundle transfer and progressive
anti-RMT surcharge are deferred follow-on WOs.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.core.database import Base


class PlayerTradeSessionStatus(enum.Enum):
    PENDING_ACCEPT = "PENDING_ACCEPT"
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    DECLINED = "DECLINED"


class PlayerTradeSession(Base):
    """Bilateral co-located trade session (one open session per player)."""

    __tablename__ = "player_trade_sessions"
    __table_args__ = (
        Index("ix_player_trade_sessions_status", "status"),
        Index("ix_player_trade_sessions_initiator", "initiator_id"),
        Index("ix_player_trade_sessions_target", "target_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    initiator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(
        Enum(PlayerTradeSessionStatus, name="player_trade_session_status"),
        nullable=False,
        default=PlayerTradeSessionStatus.PENDING_ACCEPT,
    )
    # Bumped on every stage change; confirms bind to a specific version.
    version = Column(Integer, nullable=False, default=1, server_default="1")
    initiator_confirmed_version = Column(Integer, nullable=True)
    target_confirmed_version = Column(Integer, nullable=True)
    sector_id = Column(Integer, nullable=False)
    # Optional station UUID when both docked (ship trades later); unused in v1 kernel.
    port_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Offer shape: {credits: int, commodities: {slug: qty}, ship_id: uuid|null}
    # ship_id names which hull's cargo the commodities leave/enter.
    initiator_offer = Column(JSONB, nullable=False, default=dict)
    target_offer = Column(JSONB, nullable=False, default=dict)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    settled_at = Column(DateTime(timezone=True), nullable=True)
    terminal_reason = Column(String(64), nullable=True)


class PlayerTradeLog(Base):
    """Immutable audit row written on successful settle."""

    __tablename__ = "player_trade_logs"
    __table_args__ = (
        Index("ix_player_trade_logs_initiator", "initiator_id"),
        Index("ix_player_trade_logs_target", "target_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("player_trade_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    initiator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    sector_id = Column(Integer, nullable=False)
    # Full settled manifest + offers snapshot.
    manifest = Column(JSONB, nullable=False, default=dict)
    appraised_value = Column(Integer, nullable=False, default=0)
    tax_paid = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PlayerTradeablePrice(Base):
    """Admin-maintained reference values for trade appraisal (ADR-0089)."""

    __tablename__ = "player_tradeable_prices"
    __table_args__ = (
        Index(
            "uq_player_tradeable_prices_asset_key",
            "asset_key",
            unique=True,
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_key = Column(String(64), nullable=False)
    unit_value_cr = Column(Integer, nullable=False)
    # Seed / admin note — e.g. "commodity:ore"
    category = Column(String(32), nullable=False, default="commodity")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
