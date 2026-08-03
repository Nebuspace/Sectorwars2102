"""Player-to-player trade API (ADR-0089 v1 kernel — credits + commodities)."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Dict, Optional

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.services.player_trade_service import PlayerTradeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trade", tags=["player-trade"])


class InitiateRequest(BaseModel):
    target_player_id: str


class OfferRequest(BaseModel):
    credits: int = 0
    commodities: Dict[str, int] = Field(default_factory=dict)
    ship_id: Optional[str] = None


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
