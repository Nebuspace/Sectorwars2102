"""Haggle API surface — numerical price negotiation (ADR-0079).

Endpoints (all require an authenticated player docked in the station's sector):

* ``POST /haggle/open``   — open a 4-round numerical session for (station, commodity, side, quantity).
* ``POST /haggle/offer``  — submit a per-unit offer; returns accept / counter / reject / timeout.
* ``GET  /haggle/status`` — current session / lock / cooldown for a (station, commodity, side).

On an ACCEPT the agreed per-unit price is stored on the session; the next matching
``POST /trading/buy`` or ``/trading/sell`` consumes it (single-use) in place of the
posted price, still clamped to the commodity's [0.80, 1.20] × fair band.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from src.utils.error_handling import route_internal_error

ERR_HAGGLE_OPEN_FAILED = "ERR_HAGGLE_OPEN_FAILED"
ERR_HAGGLE_OFFER_FAILED = "ERR_HAGGLE_OFFER_FAILED"
ERR_HAGGLE_STATUS_FAILED = "ERR_HAGGLE_STATUS_FAILED"
ERR_HAGGLE_NARRATIVE_OPEN_FAILED = "ERR_HAGGLE_NARRATIVE_OPEN_FAILED"
ERR_HAGGLE_NARRATIVE_SUBMIT_FAILED = "ERR_HAGGLE_NARRATIVE_SUBMIT_FAILED"
ERR_HAGGLE_NARRATIVE_STATUS_FAILED = "ERR_HAGGLE_NARRATIVE_STATUS_FAILED"

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.station import Station
from src.models.user import User
from src.services.haggle_service import HaggleService, HaggleError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/haggle", tags=["haggle"])


class HaggleOpenRequest(BaseModel):
    station_id: str
    commodity: str
    side: str = Field(..., description="'buy' (player buying) or 'sell' (player selling)")
    quantity: int = Field(..., gt=0, le=100000)


class HaggleOfferRequest(BaseModel):
    station_id: str
    commodity: str
    side: str = Field(..., description="'buy' or 'sell'")
    offer: float = Field(..., gt=0, description="Per-unit offer in credits")


class HaggleNarrativeOpenRequest(BaseModel):
    station_id: str
    commodity: str
    side: str = Field(..., description="'buy' (player buying) or 'sell' (player selling)")
    quantity: int = Field(..., gt=0, le=100000)


class HaggleNarrativeSubmitRequest(BaseModel):
    station_id: str
    commodity: str
    side: str = Field(..., description="'buy' or 'sell'")
    submission: str = Field(..., min_length=1, max_length=280)


def _station_or_404(db: Session, station_id: str) -> Station:
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


def _require_docked_here(player: Player, station: Station) -> None:
    """Haggling is a docked-at-the-desk activity — mirror the trading guards."""
    from src.services.trading_service import TradingService

    ok, reason = TradingService.can_player_trade(player, station)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)


@router.post("/open")
async def open_haggle(
    body: HaggleOpenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, body.station_id)
    _require_docked_here(current_player, station)
    try:
        card = HaggleService(db).open_session(
            current_player, station, body.commodity, body.side, body.quantity
        )
        db.commit()
        return card
    except HaggleError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.error("haggle open failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_OPEN_FAILED,
            "Failed to open haggle session",
        )


@router.post("/offer")
async def submit_offer(
    body: HaggleOfferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, body.station_id)
    _require_docked_here(current_player, station)
    try:
        result = HaggleService(db).submit_offer(
            current_player, station, body.commodity, body.side, body.offer
        )
        db.commit()
        return result
    except HaggleError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.error("haggle offer failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_OFFER_FAILED,
            "Failed to submit offer",
        )


@router.get("/status")
async def haggle_status(
    station_id: str = Query(...),
    commodity: str = Query(...),
    side: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, station_id)
    try:
        return HaggleService(db).get_status(current_player, station, commodity, side)
    except HaggleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("haggle status failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_STATUS_FAILED,
            "Failed to read haggle status",
        )


@router.post("/narrative/open")
async def open_narrative_haggle(
    body: HaggleNarrativeOpenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, body.station_id)
    _require_docked_here(current_player, station)
    try:
        card = HaggleService(db).open_narrative_session(
            current_player, station, body.commodity, body.side, body.quantity
        )
        db.commit()
        return card
    except HaggleError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.error("narrative haggle open failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_NARRATIVE_OPEN_FAILED,
            "Failed to open narrative haggle session",
        )


@router.post("/narrative/submit")
async def submit_narrative_haggle(
    body: HaggleNarrativeSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, body.station_id)
    _require_docked_here(current_player, station)
    try:
        result = HaggleService(db).submit_narrative_line(
            current_player,
            station,
            body.commodity,
            body.side,
            body.submission,
        )
        db.commit()
        return result
    except HaggleError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.error("narrative haggle submit failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_NARRATIVE_SUBMIT_FAILED,
            "Failed to submit narrative haggle line",
        )


@router.get("/narrative/status")
async def narrative_haggle_status(
    station_id: str = Query(...),
    commodity: str = Query(...),
    side: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    station = _station_or_404(db, station_id)
    try:
        return HaggleService(db).get_narrative_status(
            current_player, station, commodity, side
        )
    except HaggleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("narrative haggle status failed", exc_info=True)
        raise route_internal_error(
            ERR_HAGGLE_NARRATIVE_STATUS_FAILED,
            "Failed to read narrative haggle status",
        )
