"""ADR-0054 X-D3 -- GC-lapse 7-day liquidation window self-service routes.

The router carries its own /players prefix (matches the ADR's literal
``POST /api/v1/players/me/gc-emergency-relocation`` path) and is mounted in
api.py WITHOUT an extra prefix, yielding /api/v1/players/*. Thin wrapper over
src.services.gc_lapse_service; the other three in-window actions the ADR
names (physical safe-withdraw, NPC station buyback, voluntary surrender)
ride existing routes unmodified -- see gc_lapse_service's module docstring.
"""
import logging
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import gc_lapse_service
from src.services.gc_lapse_service import GCLapseError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/players", tags=["gc-lapse"])


class GCEmergencyRelocationRequest(BaseModel):
    asset_type: str  # "planet" | "station"
    asset_id: UUID


def _gc_lapse_http_error(e: GCLapseError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/me/gc-lapse-status")
async def gc_lapse_status(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Whether the player is currently in a GC-lapse window, whether the
    one-time emergency-relocation grant is still available, and the list of
    foreign-region holdings a relocation could target."""
    return {
        "lapsed": player.gc_lapsed_at is not None,
        "gc_lapsed_at": player.gc_lapsed_at.isoformat() if player.gc_lapsed_at else None,
        "relocation_available": (
            player.gc_lapsed_at is not None and player.gc_relocation_used_at is None
        ),
        "foreign_holdings": gc_lapse_service.list_foreign_holdings(db, player),
    }


@router.post("/me/gc-emergency-relocation")
async def gc_emergency_relocation(
    request: GCEmergencyRelocationRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """One-time, free teleport to a foreign-region holding during the 7-day
    GC-lapse liquidation window (ADR-0054 X-D3). Consumed once per lapse
    cycle; renewed on the next GC re-subscription."""
    try:
        result = gc_lapse_service.emergency_relocate(
            db, player.id, request.asset_type, request.asset_id
        )
        db.commit()
        return result
    except GCLapseError as e:
        db.rollback()
        raise _gc_lapse_http_error(e) from e
    except Exception:
        db.rollback()
        logger.exception("Failed to perform emergency relocation")
        raise HTTPException(status_code=500, detail="Failed to perform emergency relocation")
