"""
Route-optimization telemetry model (WO-SB-RO2).

Records one row per SUCCESSFUL response from either player-facing route
optimizer endpoint — ``POST /api/v1/routes/optimize`` (route_optimizer.py,
``objective`` in shortest|profit|risk|balanced) and
``POST /api/v1/ai/optimize-route`` (ai.py, ``objective='ai_trading'``).

Purely a run-log: it gives the admin NH18 dashboard (RouteOptimizationDisplay)
a real feed and lets players inspect their own history later. Recording is
best-effort — the route handlers wrap the insert in try/except so a logging
failure never fails the player's actual optimize request. Nothing reads this
table transactionally; it is append-only telemetry.
"""

from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class RouteOptimizationRun(Base):
    """One recorded call to a route-optimizer endpoint."""

    __tablename__ = "route_optimization_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 'shortest' | 'profit' | 'risk' | 'balanced' | 'ai_trading'
    objective = Column(String(32), nullable=False)

    start_sector = Column(String(64), nullable=False)
    end_sector = Column(String(64), nullable=True)

    # Ordered list of sector ids the route passes through.
    sectors = Column(JSONB, nullable=False)

    total_profit = Column(Float, nullable=False, default=0.0)
    total_distance = Column(Integer, nullable=False, default=0)
    total_time_hours = Column(Float, nullable=False, default=0.0)
    cargo_efficiency = Column(Float, nullable=False, default=0.0)
    route_confidence = Column(Float, nullable=False, default=0.0)

    status = Column(String(16), nullable=False, server_default="completed")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    player = relationship("Player", foreign_keys=[player_id])

    def __repr__(self) -> str:
        return (
            f"<RouteOptimizationRun id={self.id} player_id={self.player_id} "
            f"objective={self.objective} status={self.status}>"
        )
