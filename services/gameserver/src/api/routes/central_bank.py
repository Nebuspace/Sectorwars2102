"""Central Nexus Bank player routes (ADR-0050 / region-lifecycle.md).

Deposit paths are invoked internally by cascade orchestrators; this router
exposes the Starport-Prime withdrawal surface for credits and commodities.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.models.ship import Ship
from src.models.station import Station
from src.services import central_bank_service as bank
from src.services.central_bank_service import CentralBankError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/central-bank", tags=["central-bank"])


class WithdrawCreditsRequest(BaseModel):
    amount: int = Field(..., gt=0)


class WithdrawCommodityRequest(BaseModel):
    commodity: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


def _bank_http_error(exc: CentralBankError) -> HTTPException:
    detail: Any = exc.message
    if exc.payload:
        detail = {"detail": exc.message, **exc.payload}
    return HTTPException(status_code=exc.status_code, detail=detail)


def _load_docked_station(db: Session, player: Player) -> Station:
    if not player.is_docked or not player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station to use the Central Nexus Bank",
        )
    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if station is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Docked station not found",
        )
    return station


def _load_current_ship(db: Session, player: Player) -> Ship:
    if not player.current_ship_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an active ship to withdraw commodities",
        )
    ship = db.query(Ship).filter(Ship.id == player.current_ship_id).first()
    if ship is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current ship not found",
        )
    return ship


@router.get("/balance")
async def bank_balance(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    account = bank.get_or_create_account(db, player.id)
    return {
        "credits": int(account.credits or 0),
        "commodities": dict(account.commodities or {}),
    }


@router.post("/withdraw/credits")
async def withdraw_credits(
    request: WithdrawCreditsRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    station = _load_docked_station(db, player)
    try:
        account = bank.withdraw_credits(
            db, player, station, request.amount
        )
        db.commit()
        return {
            "withdrawn": request.amount,
            "bank_credits_remaining": int(account.credits or 0),
            "wallet_credits": int(player.credits or 0),
        }
    except CentralBankError as exc:
        db.rollback()
        raise _bank_http_error(exc) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/withdraw/commodity")
async def withdraw_commodity(
    request: WithdrawCommodityRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    station = _load_docked_station(db, player)
    ship = _load_current_ship(db, player)
    try:
        result = bank.withdraw_commodity(
            db,
            player,
            ship,
            station,
            request.commodity,
            request.quantity,
        )
        db.commit()
        return result
    except CentralBankError as exc:
        db.rollback()
        raise _bank_http_error(exc) from exc
    except Exception:
        db.rollback()
        raise
