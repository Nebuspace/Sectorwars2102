"""PendingEngagement — durable police arrival watcher (ADR-0042).

When an offense fires, route_engagement picks the squad immediately
(named officers flip to ENGAGED_PENDING_ARRIVAL) but ARRIVAL is gated on
the offending player consuming 2 turns (the cumulative
``Player.lifetime_turns_spent`` clock). Rows survive scheduler restarts
and player disconnects; a 1-minute sweep discharges/cancels/expires
them.

States: PENDING (watching) → ARRIVED (squad placed in the offender's
current sector, ENGAGED) → RESOLVED (offender left the encounter
sector); or CANCELLED (jurisdiction exit, −25 evade-arrest rep) /
EXPIRED (>24h canonical).
"""

import uuid
import enum

from sqlalchemy import Column, DateTime, String, Integer, ForeignKey, Enum, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB

from src.core.database import Base


class EngagementStatus(enum.Enum):
    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PendingEngagement(Base):
    __tablename__ = "pending_engagements"
    __table_args__ = (
        # ADR-0042: indexed for the turn-watcher and the periodic sweep.
        Index("ix_pending_engagements_player_threshold",
              "player_id", "arrival_turn_threshold"),
        Index("ix_pending_engagements_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    # attack_innocent / wanted_status / attack_police / sentinel_killed /
    # protected_sector_breach — per-type 5-turn cooldown (ADR-0042).
    offense_type = Column(String(40), nullable=False)
    # "federation" | "sentinel" — exit of this jurisdiction cancels.
    jurisdiction = Column(String(20), nullable=False)
    # Global sectors.sector_id where the offense fired.
    offense_sector_id = Column(Integer, nullable=False)
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # NPCCharacter UUIDs (strings) committed to this engagement. Empty
    # while in the no-officer grace window.
    npc_squad_ids = Column(JSONB, nullable=False, default=list)
    # Player.lifetime_turns_spent at offense time; arrival at +2.
    offense_at_turn_count = Column(Integer, nullable=False)
    arrival_turn_threshold = Column(Integer, nullable=True)
    status = Column(
        Enum(EngagementStatus, name="engagement_status"),
        nullable=False,
        default=EngagementStatus.PENDING,
    )
    # Where the squad was placed on arrival (release watch keys off it).
    arrival_sector_id = Column(Integer, nullable=True)
    # No-eligible-officer grace deadline (5–15 min, scaled).
    grace_expires_at = Column(DateTime(timezone=True), nullable=True)
    # >24h canonical idle expiry (ADR-0042).
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PendingEngagement {self.offense_type}/{self.jurisdiction} "
            f"player={self.player_id} ({self.status.name})>"
        )
