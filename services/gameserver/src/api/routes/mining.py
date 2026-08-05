"""
Mining API Routes

Player-facing endpoints for the asteroid-mining loop (FEATURES/economy/mining.md):
the per-attempt harvest action and the Astral Mining Consortium claim-license
purchase. Both delegate to ``MiningService`` (server-authoritative, atomic);
this layer only validates the request shape, supplies the authenticated player,
and maps the service's stable ``reason`` codes onto HTTP statuses. The route
patterns (Pydantic request model, ``Depends(get_current_player)``,
service-result → HTTPException) mirror ``api/routes/ship_upgrades.py`` and
``api/routes/trading.py``.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.mining_service import MiningService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining"])


class HarvestRequest(BaseModel):
    ship_id: str


class LicenseRequest(BaseModel):
    ship_id: str


# Stable MiningService reason codes that describe a missing/invalid TARGET rather
# than a precondition the player failed — these map to 404. Every other failure
# reason is a gate/affordability failure and maps to 400 (success → 200). Mirrors
# the trading/ship-upgrade convention: a malformed or absent referent is a 404,
# a "you can't do that right now" is a 400.
_NOT_FOUND_REASONS = frozenset(
    {
        "ship_not_found",
    }
)


def _status_for_reason(reason) -> int:
    """Map a MiningService failure ``reason`` code to an HTTP status.

    404 for a missing/invalid referent (see ``_NOT_FOUND_REASONS``); 400 for
    every other gate/affordability failure. A ``None``/empty reason on a failed
    result degrades to 400 (a generic bad-request gate failure)."""
    if reason in _NOT_FOUND_REASONS:
        return status.HTTP_404_NOT_FOUND
    return status.HTTP_400_BAD_REQUEST


@router.post("/harvest")
async def harvest_asteroids(
    request: HarvestRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Start an interruptible asteroid harvest in the player's current sector.

    Delegates to ``MiningService.harvest`` — gates, prepaid turn spend,
    ``Ship.status = MINING``, and a PENDING ``MiningHarvest`` row. Yields
    apply when the scheduler (or an explicit resolve) completes the row.
    Success returns ``status=in_progress`` with ``harvest_id`` /
    ``resolves_at``; yield fields stay 0 until completion."""
    result = MiningService(db).harvest(request.ship_id, player.id)
    if not result.get("success"):
        # Discard any partial side effects from a failed gate.
        db.rollback()
        reason = result.get("reason")
        raise HTTPException(
            status_code=_status_for_reason(reason),
            detail=reason or "Harvest failed",
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


@router.get("/harvest/{harvest_id}")
async def get_harvest_status(
    harvest_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Poll a harvest row owned by the authenticated player."""
    from uuid import UUID

    from src.models.mining_harvest import MiningHarvest

    try:
        hid = UUID(harvest_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_harvest_id",
        ) from exc

    row = (
        db.query(MiningHarvest)
        .filter(MiningHarvest.id == hid, MiningHarvest.player_id == player.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="harvest_not_found",
        )
    return {
        "harvest_id": str(row.id),
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "resolves_at": row.resolves_at.isoformat() if row.resolves_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "ore": row.ore_yield or 0,
        "precious_metals": row.precious_metals_yield or 0,
        "quantum_shards": row.quantum_shards_yield or 0,
        "am_rep_delta": row.am_rep_delta or 0,
        "turns_spent": row.turns_spent,
        "terminal_reason": row.terminal_reason,
    }


@router.post("/license")
async def purchase_claim_license(
    request: LicenseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase a 24h Astral Mining Consortium claim license for the player's
    current sector (FEATURES/economy/mining.md §Astral Mining Consortium claim
    licenses).

    Delegates to ``MiningService.purchase_license``, which charges the
    tier-scaled fee and writes the ``ClaimLicense`` row atomically. A failed
    purchase (e.g. not an AM-claimed sector, insufficient credits, an already
    active license) returns a stable ``reason`` code mapped onto an HTTP status;
    on success the license id / expiry / cost are returned verbatim."""
    result = MiningService(db).purchase_license(request.ship_id, player.id)
    if not result.get("success"):
        db.rollback()
        reason = result.get("reason")
        raise HTTPException(
            status_code=_status_for_reason(reason),
            detail=reason or "License purchase failed",
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


class MiningLaserUpgradeRequest(BaseModel):
    ship_id: str


@router.post("/laser-upgrade")
async def upgrade_mining_laser(
    request: MiningLaserUpgradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Buy the next Mining Laser ladder level for an equipped ship
    (FEATURES/economy/mining.md § Mining Laser ladder).

    Raises the ``level`` in ``equipment_slots["mining_laser"]`` — the value
    ``MiningService`` reads to scale ore yield, the precious-metals cap, and
    the quantum-shard trace drop. Delegates to the established
    ``ShipUpgradeService`` purchase ritual (row-lock + credit check); the route
    owns the commit. Without an exposed route the whole yield ladder is
    unreachable (harvest stays at L0)."""
    from src.services.ship_upgrade_service import ShipUpgradeService

    result = ShipUpgradeService(db).purchase_mining_laser_upgrade(
        request.ship_id, player.id
    )
    if not result.get("success"):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message") or "Mining laser upgrade failed",
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result
