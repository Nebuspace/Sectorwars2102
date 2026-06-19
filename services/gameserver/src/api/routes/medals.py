"""
Medal API routes (ADR-0028).

Player endpoint  : list my earned + available medals (player_medals JOIN medals catalog).
Admin endpoints  : grant / revoke a medal for a player.

The relational lifecycle lives in :mod:`src.services.medal_service`; the catalog
in :mod:`src.services.medal_catalog`.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player, get_current_admin
from src.models.player import Player
from src.models.user import User
from src.services.medal_service import MedalService
from src.services.medal_catalog import MEDAL_CATALOG, get_catalog_entry

router = APIRouter(
    prefix="/medals",
    tags=["medals"],
    responses={404: {"description": "Not found"}},
)


# ------------------------------------------------------------------
# Response / request models
# ------------------------------------------------------------------

class EarnedMedal(BaseModel):
    key: str
    name: str
    category: str
    description: Optional[str] = None
    icon: Optional[str] = None
    tier: Optional[str] = None
    awarded_at: Optional[str] = None
    awarded_via: Optional[str] = None
    value_at_award: Optional[int] = None


class AvailableMedal(BaseModel):
    key: str
    name: str
    category: str
    description: Optional[str] = None
    icon: Optional[str] = None
    tier: Optional[str] = None
    trigger_type: Optional[str] = None
    threshold: Optional[int] = None


class PlayerMedalsResponse(BaseModel):
    earned: List[EarnedMedal]
    available: List[AvailableMedal]
    total_earned: int
    total_available: int


class AdminGrantRequest(BaseModel):
    player_id: uuid.UUID
    medal_id: str
    reason: Optional[str] = None


class AdminRevokeRequest(BaseModel):
    player_id: uuid.UUID
    medal_id: str
    reason: Optional[str] = None


class AdminMedalActionResponse(BaseModel):
    success: bool
    changed: bool
    player_id: str
    medal_id: str
    message: str


# ------------------------------------------------------------------
# Player endpoint
# ------------------------------------------------------------------

@router.get("/me", response_model=PlayerMedalsResponse)
async def get_my_medals(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List the current player's earned and available medals."""
    medal_service = MedalService(db)
    result = medal_service.get_player_medals(player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error") or "Failed to get medals",
        )
    return PlayerMedalsResponse(
        earned=[EarnedMedal(**m) for m in result["earned"]],
        available=[AvailableMedal(**m) for m in result["available"]],
        total_earned=result["total_earned"],
        total_available=result["total_available"],
    )


# ------------------------------------------------------------------
# Admin endpoints
# ------------------------------------------------------------------

@router.post("/admin/grant", response_model=AdminMedalActionResponse)
async def admin_grant_medal(
    payload: AdminGrantRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Admin: grant a medal to a player (idempotent — no-op if already held)."""
    if not get_catalog_entry(payload.medal_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown medal_id: {payload.medal_id}",
        )
    player = db.query(Player).filter(Player.id == payload.player_id).first()
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")

    medal_service = MedalService(db)
    changed = medal_service.admin_grant(
        payload.player_id, payload.medal_id, admin.id, reason=payload.reason
    )
    db.commit()
    return AdminMedalActionResponse(
        success=True,
        changed=changed,
        player_id=str(payload.player_id),
        medal_id=payload.medal_id,
        message="Medal granted" if changed else "Player already holds this medal",
    )


@router.post("/admin/revoke", response_model=AdminMedalActionResponse)
async def admin_revoke_medal(
    payload: AdminRevokeRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Admin: revoke a medal from a player."""
    medal_service = MedalService(db)
    changed = medal_service.admin_revoke(payload.player_id, payload.medal_id)
    db.commit()
    return AdminMedalActionResponse(
        success=True,
        changed=changed,
        player_id=str(payload.player_id),
        medal_id=payload.medal_id,
        message="Medal revoked" if changed else "Player did not hold this medal",
    )
