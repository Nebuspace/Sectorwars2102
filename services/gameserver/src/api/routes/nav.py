"""
Navigation routes — ADR-0072 Phase 1.

POST /api/v1/nav/plot
  Compute a course from the player's current sector to a target sector
  through the player's known graph (visited ∪ corp-shared ∪ current sector).

The route handler follows the trading.py pattern:
  - Session = Depends(get_db)  (sync)
  - current_player: Player = Depends(get_current_player)
  - No HTTP errors for game-logic unreachable states — the shape carries
    reachable=False with optional error field per the frozen contract.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services.nav_service import NavService

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nav", tags=["nav"])


class PlotRequest(BaseModel):
    target_sector_id: int = Field(..., gt=0, description="Numeric sector number (must be a positive integer)")
    objective: str = Field(default="min_time", description="Routing objective (currently only min_time is implemented)")


@router.post("/plot")
async def plot_course(
    plot_request: PlotRequest,
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """
    Compute a course from the player's current sector to the requested target.

    Returns the frozen contract shape:

    Reachable:
      {"success": true, "reachable": true, "target_sector_id": int,
       "hops": [...], "total_turns": int}

    Unreachable (target outside known graph or disconnected component):
      {"success": true, "reachable": false, "target_sector_id": int,
       "nearest_known": {"sector_id": int, "name": str} | null}

    Unknown target (sector does not exist):
      {"success": true, "reachable": false, "target_sector_id": int,
       "nearest_known": ..., "error": "unknown sector"}

    Runaway guard (> 200 hops):
      {"success": false, "message": "..."}

    Note: objective is accepted for forward-compatibility with ADR-0072
    consciousness tiers (Awakened: MIN_RISK, Transcendent: re-plot) but is
    not yet differentiated — all plots use Dijkstra by turn-cost (min_time).
    """
    nav = NavService(db)
    result = nav.plot(current_player, plot_request.target_sector_id)
    return result
