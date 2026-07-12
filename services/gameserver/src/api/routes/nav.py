"""
Navigation routes — ADR-0072 Phase 1.

POST /api/v1/nav/plot
  Compute a course from the player's current sector to a target sector
  through the player's known graph (visited ∪ corp-shared ∪ current sector).

GET /api/v1/nav/chart
  Return the player's known navigation surface (WO-PUX-NAVCHART) for the
  cockpit NAV CHART page: sectors in the known graph, the warp/tunnel edges
  between them, and frontier stubs (id-only, each linked via `from` to the
  known sector that surfaced it — WO-NAV-CHART-FRONTIER-EDGES) for unknown
  adjacent sectors. Read-only, additive — reuses the same known-graph
  assembly as /nav/plot.

  `?bounded=true` (WO-NAV-REACH-BACKEND, opt-in, default false — the bare
  default is today's exact unbounded response, byte-identical) narrows the
  known graph with a server-computed, server-side depth bound: a DIRECTED
  BFS from the player's current sector, capped at their effective scanner
  range, over the same known-graph adjacency /nav/plot's Dijkstra walks.
  The depth is never client-supplied — only the ship's own stats drive it.
  No `current_ship` -> `bounded` has no effect (unbounded, same as today).

The route handler follows the trading.py pattern:
  - Session = Depends(get_db)  (sync)
  - current_player: Player = Depends(get_current_player)
  - No HTTP errors for game-logic unreachable states — the shape carries
    reachable=False with optional error field per the frozen contract.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
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

    Note: objective selects the routing semantics (ADR-0072 consciousness
    tiers). "min_time" (default) plots by turn-cost; "min_risk" penalizes
    low-safety hops using visit-derived safety ratings. Any unrecognised
    objective falls back to min_time.
    """
    nav = NavService(db)
    result = nav.plot(
        current_player,
        plot_request.target_sector_id,
        objective=plot_request.objective,
    )
    return result


@router.get("/chart")
async def get_nav_chart(
    bounded: bool = Query(
        False,
        description=(
            "Opt-in server-side depth bound (WO-NAV-REACH-BACKEND). Default "
            "false = today's exact unbounded chart. When true, the known "
            "graph is narrowed to what the player's current ship could "
            "actually reach, via a server-computed depth (the player's "
            "effective scanner range) — never a client-supplied number."
        ),
    ),
    db: Session = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """
    Return the player's known navigation surface for the cockpit NAV CHART
    page (WO-PUX-NAVCHART).

    {"sectors": [{"sector_id", "name", "type", "x", "y", "z", "visited",
                  "current"}, ...],
     "edges": [{"from", "to", "kind": "warp"|"tunnel"}, ...],
     "frontier": [{"id": sector_id, "from": known_sector_id}, ...]}

    Sectors are the player's known graph (visited ∪ corp-shared ∪ current —
    the same ``get_known_sector_ids`` assembly ``POST /nav/plot`` uses),
    optionally narrowed by ``bounded=true`` (see the module docstring and
    ``NavService.get_chart``/``_bound_known_ids``). Frontier entries carry
    only a bare sector_id (``id``) plus the numeric ``sector_id`` of the one
    known sector that surfaced it (``from``) — no name/type/contents of the
    frontier sector itself — for sectors adjacent to known space but not
    themselves known (or, under ``bounded=true``, known but beyond the
    reach bound). Read-only; mutates nothing.
    """
    nav = NavService(db)
    return nav.get_chart(current_player, bounded=bounded)
