"""
Ground-expedition routes (ADR-0091 §1/§3 "The at-launch expedition model").
Thin wrapper over src.services.expedition_service; every expedition is a
fresh RNG roll generated at launch. Route owns db.commit()/db.rollback()
-- the service is flush-only, matching recovery.py's convention.

The router carries its own /expeditions prefix and is mounted in api.py
WITHOUT an extra prefix, yielding /api/v1/expeditions/*.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.expedition import Expedition
from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship
from src.services import expedition_service, first_login_service
from src.services.expedition_service import ExpeditionError
from src.utils.error_handling import route_internal_error

ERR_EXPEDITIONS_LAUNCH_FAILED = "ERR_EXPEDITIONS_LAUNCH_FAILED"
ERR_EXPEDITIONS_REROLL_FAILED = "ERR_EXPEDITIONS_REROLL_FAILED"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expeditions", tags=["expeditions"])


class LaunchExpeditionRequest(BaseModel):
    planet_id: uuid.UUID
    ship_id: Optional[uuid.UUID] = None


def _serialize(expedition: Expedition) -> dict:
    return {
        "id": str(expedition.id),
        "planet_id": str(expedition.planet_id),
        "ship_id": str(expedition.ship_id) if expedition.ship_id else None,
        "status": expedition.status.value,
        "result": expedition.result,
        "demo": expedition.demo,
        "launched_at": expedition.launched_at.isoformat() if expedition.launched_at else None,
    }


def _get_owned_planet(db: Session, planet_id: uuid.UUID) -> Planet:
    planet = db.query(Planet).filter(Planet.id == planet_id).first()
    if planet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planet not found")
    return planet


def _get_owned_ship(db: Session, player: Player, ship_id: uuid.UUID) -> Ship:
    ship = (
        db.query(Ship)
        .filter(Ship.id == ship_id, Ship.owner_id == player.id)
        .first()
    )
    if ship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    return ship


@router.post("/launch")
async def launch_expedition(
    request: LaunchExpeditionRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Dispatch a new ground expedition (ADR-0091 §3). Costs turns +
    credits, subject to the per-player concurrency cap of 3 active
    (non-demo) expeditions."""
    planet = _get_owned_planet(db, request.planet_id)
    ship = _get_owned_ship(db, player, request.ship_id) if request.ship_id else None
    # ADR-0091 M38: a first-colony player's expedition on their designated
    # starter planet is forced-success/guaranteed-good/cost-waived. {} on
    # every other planet or after colony #1, so this is a no-op merge there.
    overrides = first_login_service.get_first_colony_expedition_overrides(db, player, planet)
    try:
        expedition = expedition_service.roll_expedition(db, player, planet, ship, **overrides)
        db.commit()
        return _serialize(expedition)
    except ExpeditionError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        logger.exception("Failed to launch expedition")
        raise route_internal_error(
            ERR_EXPEDITIONS_LAUNCH_FAILED,
            "Failed to launch expedition",
        )


@router.get("/{expedition_id}/status")
async def expedition_status(
    expedition_id: uuid.UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Look up a single expedition's resolved (or PENDING) status."""
    expedition = (
        db.query(Expedition)
        .filter(Expedition.id == expedition_id, Expedition.player_id == player.id)
        .first()
    )
    if expedition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not found")
    return _serialize(expedition)


@router.get("")
async def list_expeditions(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List the current player's expeditions, newest first."""
    expeditions = (
        db.query(Expedition)
        .filter(Expedition.player_id == player.id)
        .order_by(Expedition.launched_at.desc())
        .all()
    )
    return {"expeditions": [_serialize(e) for e in expeditions]}


@router.post("/{expedition_id}/reroll")
async def reroll_expedition(
    expedition_id: uuid.UUID,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Launch a fresh, independent expedition on the same planet/ship as
    a prior expedition, at the same base price (ADR §3: no discount --
    "no escalating Kth-scout")."""
    prior = (
        db.query(Expedition)
        .filter(Expedition.id == expedition_id, Expedition.player_id == player.id)
        .first()
    )
    if prior is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not found")
    planet = _get_owned_planet(db, prior.planet_id)
    ship = _get_owned_ship(db, player, prior.ship_id) if prior.ship_id else None
    try:
        expedition = expedition_service.reroll_expedition(db, player, planet, ship)
        db.commit()
        return _serialize(expedition)
    except ExpeditionError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        db.rollback()
        logger.exception("Failed to reroll expedition")
        raise route_internal_error(
            ERR_EXPEDITIONS_REROLL_FAILED,
            "Failed to reroll expedition",
        )
