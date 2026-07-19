"""
Quantum Drive routes — the Warp Jumper's scan / refine / jump loop.

Canon reference: sw2102-docs ADR-0030 (Quantum Jump multi-step commit),
ADR-0031 (fuzzy long-range disclosure), ADR-0009 (refining venue rule).
Thin wrappers over src.services.quantum_service; all 4xx errors carry a
human-readable {detail} string.

The router carries its own /quantum prefix (construction.py precedent) and
is mounted in api.py WITHOUT an extra prefix, yielding /api/v1/quantum/*.
Mounting is owned by Section B — this module only exposes ``router``.
"""
import logging
from datetime import datetime, UTC
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import quantum_service
from src.services.quantum_service import QuantumError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quantum", tags=["quantum"])


# Request models

class BearingRequest(BaseModel):
    """Shared bearing-and-band payload for scan and jump (ADR-0030).

    yaw_deg: 0-360, counterclockwise from +x in the galactic xy-plane.
    pitch_deg: -90..90, elevation toward +z.
    """
    yaw_deg: float = Field(..., ge=0.0, le=360.0)
    pitch_deg: float = Field(..., ge=-90.0, le=90.0)
    range_band: Literal["near", "mid", "far", "extended"]


class ScanRequest(BearingRequest):
    pass


class JumpRequest(BearingRequest):
    pass


class RefineChargeRequest(BaseModel):
    """Empty body — the venue and resources are validated server-side."""
    pass


class HarvestRequest(BaseModel):
    """Empty body — the nebula sector, fitted harvester, and cooldown are all
    validated server-side from the player's current state."""
    pass


async def _emit_quantum_harvest(
    user_id: Any,
    sector_id: int,
    nebula_type: str,
    shards: int,
    crit: bool,
) -> None:
    """Push canon Resolution step 6's real-time harvest event to the
    harvesting player's socket (quantum-resources.md § Resolution: "Emit a
    real-time event on the WebSocket bus so the client UI updates without
    polling").

    DEFENSIVE: called from the route AFTER db.commit() — this body swallows
    any failure so a dead socket can never disturb an already-committed
    harvest (trading.py's _emit_transaction_completed precedent). PERSONAL
    ONLY: send_personal_message delivers exclusively to this user's socket,
    keyed on str(user_id) (movement_service.py:484-505 precedent)."""
    try:
        from src.services.websocket_service import connection_manager
        await connection_manager.send_personal_message(
            str(user_id),
            {
                "type": "quantum_harvest",
                "timestamp": datetime.now(UTC).isoformat(),
                "sector_id": sector_id,
                "nebula_type": nebula_type,
                "shards": shards,
                "crit": crit,
            },
        )
    except Exception:
        logger.debug("quantum_harvest WS push skipped", exc_info=True)


# Endpoints

@router.get("/status")
async def quantum_status(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Quantum drive status for the client console: resource wallet,
    loaded charges, cooldowns, and jump readiness."""
    return quantum_service.get_status(db, player)


@router.get("/minimap")
async def quantum_minimap(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Astrogation chart (ADR-0030 Phase 1): anonymous sector positions
    relative to the pilot's sector, within ~25 hop-units. Read-only —
    no cost, no cooldown, available docked. Per ADR-0031 it discloses
    positions ONLY (no ids, no type/activity/presence); the payload's
    complete_radius_spacings reports how far the chart is complete when
    the sector cap truncates it."""
    try:
        return quantum_service.get_minimap(db, player)
    except QuantumError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/scan")
async def quantum_scan(
    request: ScanRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phase 1 — long-range quantum sweep. 5 turns (+1 Quantum Shard for
    the far band; extended band needs Sensor L3). Returns fuzzy readings
    that expire after 10 real-minutes."""
    try:
        return quantum_service.scan(
            db, player.id, request.yaw_deg, request.pitch_deg, request.range_band
        )
    except QuantumError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/refine-charge")
async def quantum_refine_charge(
    request: RefineChargeRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Refine 1 Quantum Shard into 1 Quantum Charge on the piloted Warp
    Jumper. Requires being docked at a Class-3+ station or SpaceDock."""
    try:
        return quantum_service.refine_charge(db, player.id)
    except QuantumError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/jump")
async def quantum_jump(
    request: JumpRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Phases 2-3 — commit and resolve. Consumes 1 Quantum Charge + 50
    turns and starts the 24h jump cooldown regardless of outcome; lands at
    a candidate sector or misfires onto the bearing line with 5% max-hull
    damage (uninsured)."""
    try:
        return quantum_service.jump(
            db, player.id, request.yaw_deg, request.pitch_deg, request.range_band
        )
    except QuantumError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/harvest")
async def quantum_harvest(
    request: HarvestRequest = None,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Harvest Quantum Shards from a nebula sector. Requires a fitted Quantum
    Field Harvester, being in a NEBULA sector whose cluster carries a nebula
    type, and the 2h per-ship harvest cooldown to be clear. Rolls the canon
    shard yield (with a 2% crit) and arms the cooldown.

    THIS ROUTE OWNS THE COMMIT: harvest_nebula flushes only, so a successful
    harvest must commit here or the credited shards / armed cooldown silently
    roll back; any failure rolls back. Canon Resolution step 6: a successful,
    committed harvest also emits a real-time 'quantum_harvest' WebSocket
    event to the harvesting player — post-commit and non-fatal, so a dead
    socket never turns a good harvest into a 500."""
    try:
        result = quantum_service.harvest_nebula(db, player.id)
        db.commit()
        await _emit_quantum_harvest(
            player.user_id,
            player.current_sector_id,
            result["nebula_type"],
            result["shard_yield"],
            result["crit"],
        )
        return result
    except QuantumError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        raise
