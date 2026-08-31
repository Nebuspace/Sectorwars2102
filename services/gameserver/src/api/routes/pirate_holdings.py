"""Pirate-holding raid routes (LEG-1105).

Canon reference: SYSTEMS/pirate-holding-raid.md — concurrent-attacker
arbitration / G-F2 lock acquisition entry point only (no capture).
"""
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.player import Player
from src.models.team import Team
from src.models.user import User
from src.services import pirate_holding_raid_service
from src.services.pirate_holding_raid_service import PirateHoldingRaidError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pirate-holdings", tags=["pirate-holdings"])


def _load_player_with_team(db: Session, player_id: _uuid.UUID) -> Player:
    player = (
        db.query(Player)
        .options(joinedload(Player.team).joinedload(Team.members))
        .filter(Player.id == player_id)
        .first()
    )
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.post("/{holding_id}/raid/initiate")
async def initiate_pirate_holding_raid(
    holding_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Begin a pirate-holding raid by acquiring the G-F2 combat lock (Outpost/
    Stronghold) or acknowledging permissive Camp entry. Does not capture."""
    try:
        holding_uuid = _uuid.UUID(str(holding_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Pirate holding not found")

    player = _load_player_with_team(db, current_player.id)
    try:
        result = pirate_holding_raid_service.initiate_raid(db, holding_uuid, player)
        db.commit()
    except PirateHoldingRaidError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return result
