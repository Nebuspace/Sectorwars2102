"""
Stranding-recovery routes -- the Federation distress beacon (any hull, any
sector, -10 Terran Federation rep, 24h cooldown) and the Warp Jumper's
Slipdrive (quantum_jump_capable hulls only, multi-turn charge, fuel cost
scaled by graph distance).

Canon reference: sw2102-docs FEATURES/galaxy/sectors.md § "Recovery from
one-way stranding", FEATURES/gameplay/movement.md § "Cross-region travel"
#3, ADR-0034 (one-way warp design). Thin wrappers over
src.services.distress_service / src.services.slipdrive_service; all 4xx
errors carry a human-readable {detail} string (cooldown violations also
carry cooldown_until/remaining_seconds in the body).

The router carries its own /recovery prefix (quantum.py precedent) and is
mounted in api.py WITHOUT an extra prefix, yielding /api/v1/recovery/*.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import distress_service, slipdrive_service
from src.services.distress_service import DistressError
from src.services.slipdrive_service import SlipdriveError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recovery", tags=["recovery"])


class _EmptyRequest(BaseModel):
    """Empty body -- everything is validated server-side from the
    player's current state."""
    pass


def _distress_http_error(e: DistressError) -> HTTPException:
    body: Dict[str, Any] = {"detail": str(e)}
    body.update(e.payload)
    return HTTPException(status_code=e.status_code, detail=body)


@router.get("/status")
async def recovery_status(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Combined distress-beacon + Slipdrive status for the recovery
    console. Never 500s on an empty history (a player who has used
    neither tool reads as fully available/idle)."""
    return {
        "distress_beacon": distress_service.get_status(db, player),
        "slipdrive": slipdrive_service.get_status(db, player),
    }


@router.post("/distress-beacon")
async def fire_distress_beacon(
    request: _EmptyRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Federation distress beacon: free transport to the nearest fedspace
    sector, -10 Terran Federation reputation, 24h cooldown. Usable on any
    hull, from any sector -- the panic button."""
    try:
        result = distress_service.use_distress_beacon(db, player.id)
        db.commit()
        return result
    except DistressError as e:
        db.rollback()
        raise _distress_http_error(e)
    except Exception:
        db.rollback()
        raise


@router.post("/slipdrive/begin")
async def begin_slipdrive_charge(
    request: _EmptyRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phase 1 -- spin up the Slipdrive. Debits 3 turns immediately
    (non-refundable) and arms the charge deadline."""
    try:
        result = slipdrive_service.begin_charge(db, player.id)
        db.commit()
        return result
    except SlipdriveError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise


@router.post("/slipdrive/complete")
async def complete_slipdrive_charge(
    request: _EmptyRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phase 2 -- resolve a ready charge: teleport to the nearest non-sink
    sector, charging fuel scaled by hop distance. Rejected early (charge
    still ticking) or if movement since begin() cancelled it (no refund)."""
    try:
        result = slipdrive_service.complete_charge(db, player.id)
        db.commit()
        return result
    except SlipdriveError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise
