"""
Player Analytics Models
Tracks comprehensive player metrics and analytics data
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base


class PlayerSession(Base):
    """
    Tracks individual player login sessions for analytics
    """
    __tablename__ = "player_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False, default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)  # Calculated when session ends
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent = Column(String(500), nullable=True)
    actions_performed = Column(Integer, nullable=False, default=0)
    sectors_visited = Column(JSONB, nullable=False, default=list)
    credits_earned = Column(Integer, nullable=False, default=0)
    credits_spent = Column(Integer, nullable=False, default=0)
    
    # Relationships
    player = relationship("Player")

    def __repr__(self):
        return f"<PlayerSession {self.player_id} - {self.start_time}>"


class PlayerAnalyticsSnapshot(Base):
    """
    Periodic snapshots of player analytics data for trend analysis
    """
    __tablename__ = "player_analytics_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_time = Column(DateTime(timezone=True), nullable=False, default=func.now())
    snapshot_type = Column(String(50), nullable=False)  # 'hourly', 'daily', 'weekly'
    
    # Player counts
    total_players = Column(Integer, nullable=False, default=0)
    active_players = Column(Integer, nullable=False, default=0)
    online_players = Column(Integer, nullable=False, default=0)
    new_players_today = Column(Integer, nullable=False, default=0)
    new_players_week = Column(Integer, nullable=False, default=0)
    
    # Economic metrics
    total_credits_circulation = Column(Integer, nullable=False, default=0)
    average_credits_per_player = Column(Float, nullable=False, default=0.0)
    total_ships = Column(Integer, nullable=False, default=0)
    total_planets = Column(Integer, nullable=False, default=0)
    total_ports = Column(Integer, nullable=False, default=0)
    
    # Activity metrics
    average_session_time = Column(Float, nullable=False, default=0.0)
    total_actions_today = Column(Integer, nullable=False, default=0)
    player_retention_rate_7d = Column(Float, nullable=False, default=0.0)
    player_retention_rate_30d = Column(Float, nullable=False, default=0.0)
    
    # Security metrics
    suspicious_activity_alerts = Column(Integer, nullable=False, default=0)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    
    # Detailed breakdown data
    player_by_status = Column(JSONB, nullable=False, default=dict)  # {'active': 100, 'inactive': 50}
    ships_by_type = Column(JSONB, nullable=False, default=dict)
    planets_by_type = Column(JSONB, nullable=False, default=dict)
    activity_by_hour = Column(JSONB, nullable=False, default=dict)
    
    def __repr__(self):
        return f"<PlayerAnalyticsSnapshot {self.snapshot_type} - {self.snapshot_time}>"


class PlayerActivity(Base):
    """
    Tracks detailed player actions for analytics and security monitoring
    """
    __tablename__ = "player_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("player_sessions.id", ondelete="CASCADE"), nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=func.now())
    
    # Activity details
    activity_type = Column(String(100), nullable=False)  # 'login', 'move', 'trade', 'attack', etc.
    description = Column(String(500), nullable=True)
    sector_id = Column(Integer, nullable=True)
    target_id = Column(String(255), nullable=True)  # ID of target (player, planet, port, etc.)
    
    # Economic impact
    credits_involved = Column(Integer, nullable=False, default=0)
    items_involved = Column(JSONB, nullable=True)  # Goods, ships, etc.
    
    # Risk assessment
    risk_score = Column(Integer, nullable=False, default=0)  # 0-100 scale
    flagged_for_review = Column(Boolean, nullable=False, default=False)
    
    # Metadata
    activity_metadata = Column(JSONB, nullable=True)  # Additional context data
    
    # Relationships
    player = relationship("Player")
    session = relationship("PlayerSession")

    def __repr__(self):
        return f"<PlayerActivity {self.activity_type} - {self.player_id} - {self.timestamp}>"


class PlayerReEngagement(Base):
    """
    Re-engagement queue (WO-RE2).

    One row per player flagged at-risk by the nightly at-risk-signal sweep
    (``RetentionService.compute_player_signals`` driven by
    ``npc_scheduler_service._run_retention_sweep_async``). The row records WHICH
    of the 7 canonical at-risk signals (OPERATIONS/retention.md "At-risk
    signals") tripped for the player, the per-signal threshold metadata used to
    decide it, and when the decision was computed.

    Additive, durable, and idempotent per canonical day: the sweep computes
    signals READ-ONLY from PlayerActivity / PlayerSession (it never mutates the
    activity tables) and the ONLY write is upserting one OPEN row per flagged
    player. A row is OPEN while the player is still at-risk and uncontacted; the
    re-engagement campaign layer (email / ARIA welcome-back / turn bonus, see
    OPERATIONS/retention.md "Re-engagement campaigns") closes it.
    """
    __tablename__ = "player_re_engagement_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The signal label(s) that tripped for this player, e.g.
    # ["dormant_session", "social_isolation"]. Canonical labels only.
    signals = Column(JSONB, nullable=False, default=list)
    # Per-signal evidence: {signal_label: {threshold, observed, ...}} — the
    # threshold each tripped signal used + the observed value, for auditing and
    # campaign targeting. Read-only provenance; never PII.
    signal_detail = Column(JSONB, nullable=False, default=dict)
    # OPEN (awaiting / eligible for re-engagement) | CONTACTED | RESOLVED.
    status = Column(String(20), nullable=False, default="OPEN", server_default="OPEN")
    computed_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    # Canonical-day index the flag was last (re)computed on — lets the sweep
    # refresh an existing OPEN row's signals once per canonical day in place.
    computed_day = Column(Integer, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    player = relationship("Player")

    __table_args__ = (
        # One OPEN row per player is a DB invariant (matches the migration's
        # partial unique index) — a player may carry past RESOLVED rows but
        # only one live OPEN one; the sweep upserts that single OPEN row.
        Index(
            "uq_player_re_engagement_open",
            "player_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_player_re_engagement_queue_status", "status"),
    )

    def __repr__(self):
        return (
            f"<PlayerReEngagement {self.player_id} "
            f"signals={self.signals} status={self.status}>"
        )


# Add the relationships to existing Player model (this would be added to player.py)
"""
Add these to the Player model in player.py:

    # Analytics relationships
    sessions = relationship("PlayerSession", back_populates="player", cascade="all, delete-orphan")
    activities = relationship("PlayerActivity", cascade="all, delete-orphan")
"""