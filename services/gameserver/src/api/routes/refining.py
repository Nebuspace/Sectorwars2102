"""
Refining routes — the player path to Quantum Crystals (Shard → Crystal 5:1).

Canon reference: sw2102-docs quantum-resources.md (Shard→Crystal refining)
+ ADR-0009 (refining venue rule). Thin wrapper over
src.services.refining_service; the 400 carries a human-readable {detail}.

DISTINCT from /quantum/refine-charge (a 1:1 Shard → jump *Charge*). This is
the 5 Shards + 10,000 cr → 1 *Crystal* wallet conversion.

The router carries its own /refining prefix (quantum.py precedent) and is
mounted in src/api/api.py WITHOUT an extra prefix, yielding
/api/v1/refining/*.

DEFERRED — the documented 24h refine *queue* is not built here; this ships
the instant kernel. A queued follow-up would add a RefineJob row (player_id,
started_at, ready_at = started_at + REFINE_QUEUE_HOURS, claimed) plus a
claim endpoint / scheduler sweep that flips the crystal credit at ready_at;
the instant path can then become the "no-queue, pay-now" tier.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import refining_service
from src.services.refining_service import RefiningError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/refining", tags=["refining"])


class RefineRequest(BaseModel):
    """Empty body — the venue and resources are validated server-side from
    the player's current state."""
    pass


@router.post("/refine")
async def refine_crystal(
    request: RefineRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Refine 5 Quantum Shards + 10,000 cr into 1 Quantum Crystal at a
    Class-3+ station or SpaceDock. The ONLY player-driven source of Quantum
    Crystals (otherwise combat-loot / admin grant only).

    THIS ROUTE OWNS THE COMMIT: refining_service.refine flushes only, so a
    successful refine must commit here or the spent shards/credits and the
    new crystal silently roll back; any failure rolls back.
    """
    try:
        result = refining_service.refine(db, player.id)
        db.commit()
        return result
    except RefiningError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise
