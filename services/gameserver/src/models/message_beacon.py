"""MessageBeacon model -- WO-P4-play-beacon-kernel, canon:
FEATURES/gameplay/message-beacons.md (Status: previously 📐 Design-only).

A physical "message in a bottle" a player deploys in a sector -- discoverable
by traversal, not directory lookup. Orthogonal to the persistent Message
inbox (DATA_MODELS/player.md) and the realtime chat bus. Full schema built
as one additive whole per message-beacons.md:60-85's table.

`read_once` is 📐 Design-only per canon (:73, :28) -- the column exists and
the service supports it (message_beacon_service.py), but canon itself
flags initial launch may ship with `read_once = false` only; the flag is
cheap to support so this WO ships it live rather than half-building it.
"""
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class MessageBeacon(Base):
    """The central row for every deployed beacon (message-beacons.md:60-85).

    `(region_id, sector_id)` is the canonical sector identity (Sector.
    sector_id is globally unique today, but the compound pair is what canon
    specifies and what a future per-region-local numbering scheme would
    need) -- the dominant query ("what beacons are in this sector?") filters
    on both.
    """

    __tablename__ = "message_beacons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    region_id = Column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE"), nullable=False,
    )
    sector_id = Column(Integer, nullable=False)

    deployer_player_id = Column(
        UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False,
    )
    # Snapshot at deploy time (message-beacons.md:70) -- the message survives
    # the deployer renaming or going inactive; never re-derived from a live
    # Player lookup.
    deployer_nickname_at_deploy = Column(String(50), nullable=False)

    message = Column(String(500), nullable=False)

    # NULL = never expires (canon default).
    expiry = Column(DateTime(timezone=True), nullable=True)

    # 📐 Design-only per canon -- see module docstring.
    read_once = Column(Boolean, nullable=False, default=False, server_default="false")
    read_count = Column(Integer, nullable=False, default=0, server_default="0")

    deployed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_read_at = Column(DateTime(timezone=True), nullable=True)

    region = relationship("Region")
    deployer = relationship("Player")

    __table_args__ = (
        # The dominant query: "what beacons are in this sector?"
        Index("idx_message_beacon_region_sector", "region_id", "sector_id"),
        # Deployer's beacon-management UI ("My Beacons" screen, canon:133).
        Index("idx_message_beacon_deployer", "deployer_player_id", "deployed_at"),
        # The periodic expiry tick scans this -- partial index, only rows
        # that actually expire (message-beacons.md:81 "WHERE expiry IS NOT
        # NULL").
        Index(
            "idx_message_beacon_expiry", "expiry",
            postgresql_where=text("expiry IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MessageBeacon {self.id} sector={self.sector_id} "
            f"deployer={self.deployer_nickname_at_deploy!r}>"
        )
