from sqlalchemy import Column, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class PlayerLifetimeStats(Base):
    """Persisted, incrementally-updated mirror of the achievement counters
    ranking_service.get_player_stats() otherwise recomputes ad hoc on every
    call (COUNT/SUM scans over MarketTransaction/CombatLog/ARIAExplorationMap/
    Planet — see FEATURES/gameplay/ranking.md 'lifetime-stats table' gap).

    One row per player, updated incrementally at the existing rank-point
    award call sites (trading.py buy/sell, combat_service.py kill resolver)
    rather than recomputed from scratch. Backfilled once from existing data
    for players who predate this table.
    """
    __tablename__ = "player_lifetime_stats"

    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)
    total_trades = Column(Integer, nullable=False, default=0)
    combat_victories = Column(Integer, nullable=False, default=0)
    sectors_visited = Column(Integer, nullable=False, default=0)
    planets_owned = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    player = relationship("Player", foreign_keys=[player_id])

    def __repr__(self):
        return f"<PlayerLifetimeStats player={self.player_id} trades={self.total_trades} victories={self.combat_victories}>"
