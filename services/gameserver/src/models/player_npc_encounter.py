"""Player–NPC co-presence encounter counts (LEG-3961).

Canon: sw2102-docs/SYSTEMS/npc-scheduler.md § Player-NPC encounter recording —
tracks how often a player has shared a sector with a named on-duty NPC
(without initiating combat).
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class PlayerNpcEncounter(Base):
    __tablename__ = "player_npc_encounters"

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        primary_key=True,
    )
    npc_character_id = Column(
        UUID(as_uuid=True),
        ForeignKey("npc_characters.id", ondelete="CASCADE"),
        primary_key=True,
    )
    count = Column(Integer, nullable=False, default=1)
    last_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_sector_id = Column(Integer, nullable=False)

    player = relationship("Player", foreign_keys=[player_id])
    npc_character = relationship("NPCCharacter", foreign_keys=[npc_character_id])

    def __repr__(self) -> str:
        return (
            f"<PlayerNpcEncounter player={self.player_id} "
            f"npc={self.npc_character_id} count={self.count}>"
        )
