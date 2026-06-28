"""
Registry API Routes

Black-market planet registry lookup. A commander with underworld standing
(personal_reputation < 0) can pay an informant to surface another player's
non-clandestine holdings — name, sector, type, and registration status.

Registration status lives at planet.active_events['registration_status'] in
{clandestine|registered|chartered}. A missing key is treated as 'registered'
(visible); only an explicit 'clandestine' marking hides a planet from the
registry.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.user import User
from src.models.planet import Planet, player_planets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/registry", tags=["registry"])

# Informant fee for a successful registry lookup (a target with at least one
# non-clandestine planet on record).
LOOKUP_FEE = 50000


class RegistryLookupRequest(BaseModel):
    playerName: str


@router.post("/lookup")
async def registry_lookup(
    request: RegistryLookupRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Surface a target commander's non-clandestine planets via an underworld
    informant.

    Only commanders with negative personal reputation have the contacts to make
    this call. A successful lookup (the target has at least one visible planet)
    costs LOOKUP_FEE credits. Unknown names (404) and targets with no visible
    holdings (empty list) are never charged.
    """
    # Lock the caller's row so concurrent lookups can't double-spend the fee.
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    if player.personal_reputation >= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have no underworld contacts for that.",
        )

    # Resolve the target by case-insensitive username via the users join.
    target = (
        db.query(Player)
        .join(User, Player.user_id == User.id)
        .filter(func.lower(User.username) == func.lower(request.playerName))
        .first()
    )
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such commander on record.",
        )

    # Gather the target's owned planets through the association table
    # (mirrors planetary_service.get_player_planets).
    target_planets = (
        db.query(Planet)
        .join(player_planets, Planet.id == player_planets.c.planet_id)
        .filter(player_planets.c.player_id == target.id)
        .all()
    )

    # Read registration_status safely — active_events may be a dict (current),
    # an empty list (column default), or a legacy non-empty list.
    def _reg_status(p):
        ev = p.active_events
        return ev.get("registration_status") if isinstance(ev, dict) else None

    # Exclude clandestine planets; a missing/other status is visible.
    visible = [p for p in target_planets if _reg_status(p) != "clandestine"]

    # Nothing to sell — no charge.
    if not visible:
        return {"planets": []}

    if player.credits < LOOKUP_FEE:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient credits. The informant wants {LOOKUP_FEE}, you have {player.credits}.",
        )

    player.credits -= LOOKUP_FEE
    db.commit()

    logger.info(
        f"Player {player.id} paid {LOOKUP_FEE} for a registry lookup on "
        f"player {target.id} ({len(visible)} planet(s) returned)"
    )

    return {
        "planets": [
            {
                "name": p.name,
                "sectorId": int(p.sector_id),
                "planetType": p.planet_type or "terran",
                "registrationStatus": _reg_status(p) or "registered",
            }
            for p in visible
        ]
    }
