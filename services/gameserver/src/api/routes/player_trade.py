"""Player-to-player trade API (ADR-0089 — credits, commodities, ship-bundle)."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.player_trade_service import PlayerTradeService
from src.services.fuel_delivery_service import deliver_fuel, FuelDeliveryError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trade", tags=["player-trade"])


class InitiateRequest(BaseModel):
    target_player_id: str


class OfferRequest(BaseModel):
    credits: int = 0
    commodities: Dict[str, int] = Field(default_factory=dict)
    ship_id: Optional[str] = None
    ships: List[str] = Field(default_factory=list)


def _commit_or_400(db: Session, result: Dict[str, Any]) -> Dict[str, Any]:
    if not result.get("success"):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("reason") or "trade_failed",
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


@router.post("/initiate")
async def initiate_trade(
    body: InitiateRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    try:
        target_id = UUID(body.target_player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_target_id") from exc
    result = PlayerTradeService(db).initiate(player.id, target_id)
    return _commit_or_400(db, result)


@router.post("/{session_id}/accept")
async def accept_trade(
    session_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    result = PlayerTradeService(db).accept(UUID(session_id), player.id)
    return _commit_or_400(db, result)


@router.post("/{session_id}/decline")
async def decline_trade(
    session_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    result = PlayerTradeService(db).decline(UUID(session_id), player.id)
    return _commit_or_400(db, result)


@router.post("/{session_id}/offer")
async def stage_offer(
    session_id: str,
    body: OfferRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    offer = {
        "credits": body.credits,
        "commodities": body.commodities,
        "ship_id": body.ship_id,
        "ships": body.ships,
    }
    result = PlayerTradeService(db).stage_offer(UUID(session_id), player.id, offer)
    return _commit_or_400(db, result)


@router.post("/{session_id}/confirm")
async def confirm_trade(
    session_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    result = PlayerTradeService(db).confirm(UUID(session_id), player.id)
    return _commit_or_400(db, result)


@router.post("/{session_id}/cancel")
async def cancel_trade(
    session_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    result = PlayerTradeService(db).cancel(UUID(session_id), player.id)
    return _commit_or_400(db, result)


@router.get("/open")
async def get_open_trade(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Return the caller's open trade session, if any."""
    if not player.open_trade_session_id:
        return {"success": True, "session": None}
    result = PlayerTradeService(db).get(player.open_trade_session_id, player.id)
    if not result.get("success"):
        return {"success": True, "session": None}
    return result


class DeliverFuelRequest(BaseModel):
    recipient_player_id: str
    fuel_amount: int = Field(..., ge=1)
    payment_credits: int = Field(..., ge=0)


@router.post("/deliver-fuel")
async def deliver_fuel_route(
    body: DeliverFuelRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Immediate same-sector fuel-for-credits handoff (WO-GWQ-STRANDING-2 —
    "conversely you could pay for someone to deliver you fuel"). Wraps
    fuel_delivery_service.deliver_fuel, the KERNEL primitive shipped
    unwired until this route (movement.md "Paid fuel delivery"). No
    request/board/escrow — both players must already be in the same
    sector; the caller is the deliverer, the fuel moves onto the
    recipient's OWN ship (they still have to fly their own Slipdrive
    escape), and the payment moves from recipient to caller.
    """
    try:
        recipient_id = UUID(body.recipient_player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_recipient_id") from exc

    try:
        result = deliver_fuel(
            db,
            deliverer_player_id=player.id,
            recipient_player_id=recipient_id,
            fuel_amount=body.fuel_amount,
            payment_credits=body.payment_credits,
        )
    except FuelDeliveryError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return result


@router.get("/{session_id}")
async def get_trade(
    session_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    result = PlayerTradeService(db).get(UUID(session_id), player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND
            if result.get("reason") == "session_not_found"
            else status.HTTP_400_BAD_REQUEST,
            detail=result.get("reason") or "trade_failed",
        )
    return result
