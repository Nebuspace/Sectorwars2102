"""PlayerCentralBankAccount — Central Nexus Bank (ADR-0050 / DATA_MODELS/player.md).

Region-independent depository operated by the Galactic Concord. One account
per player. Receives cascade planet-safe transfers and station
credit-compensation fallbacks; players withdraw at Starport Prime docks.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class PlayerCentralBankAccount(Base):
    """Per-player Central Nexus Bank account (ADR-0050 schema)."""

    __tablename__ = "player_central_bank_accounts"

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        primary_key=True,
    )
    credits = Column(Integer, nullable=False, default=0, server_default="0")
    commodities = Column(JSONB, nullable=False, default=dict, server_default="{}")
    # Append-only audit entries: {timestamp, type, amount, source, notes,
    # access_override}. Cascade deposits set access_override=true (ADR-0054 X-I3).
    ledger = Column(JSONB, nullable=False, default=list, server_default="[]")
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    player = relationship("Player", foreign_keys=[player_id])

    def __repr__(self):
        return (
            f"<PlayerCentralBankAccount player={self.player_id} "
            f"credits={self.credits}>"
        )
