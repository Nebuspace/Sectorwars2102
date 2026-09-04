"""Pirate-holding routes (LEG-1105 / LEG-4109).

Canon reference: SYSTEMS/pirate-holding-raid.md — concurrent-attacker
arbitration / G-F2 lock acquisition entry point, plus discovery GETs
(list by sector + by-id) for player raid UI.
"""
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.pirate_holding import PirateHolding
from src.models.player import Player
from src.models.team import Team
from src.models.user import User
from src.services import pirate_ecosystem_service as pes
from src.services import pirate_holding_raid_service
from src.services.pirate_ecosystem_service import TIER_WEIGHT
from src.services.pirate_holding_raid_service import PirateHoldingRaidError
from src.utils.error_handling import route_internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pirate-holdings", tags=["pirate-holdings"])

ERR_PIRATE_HOLDINGS_LIST_FAILED = "ERR_PIRATE_HOLDINGS_LIST_FAILED"
ERR_PIRATE_HOLDINGS_GET_FAILED = "ERR_PIRATE_HOLDINGS_GET_FAILED"
ERR_PIRATE_HOLDINGS_CAPTURE_FAILED = "ERR_PIRATE_HOLDINGS_CAPTURE_FAILED"


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


def _holding_discovery_payload(holding: PirateHolding) -> dict:
    return {
        "id": str(holding.id),
        "tier": holding.tier.value if holding.tier is not None else None,
        "sector_id": holding.sector_id,
    }


@router.get("")
async def list_pirate_holdings(
    sector_id: int = Query(..., description="Global sectors.sector_id anchor"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """List pirate holdings anchored in a sector (id + tier + sector_id only)."""
    try:
        rows = (
            db.query(PirateHolding)
            .filter(PirateHolding.sector_id == sector_id)
            .all()
        )
        return [_holding_discovery_payload(h) for h in rows]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list pirate holdings for sector_id=%s", sector_id)
        raise route_internal_error(
            ERR_PIRATE_HOLDINGS_LIST_FAILED,
            "Failed to list pirate holdings",
        )


@router.get("/{holding_id}")
async def get_pirate_holding(
    holding_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Return minimal discovery fields for one pirate holding."""
    try:
        try:
            holding_uuid = _uuid.UUID(str(holding_id))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=404, detail="Pirate holding not found")

        holding = (
            db.query(PirateHolding).filter(PirateHolding.id == holding_uuid).first()
        )
        if holding is None:
            raise HTTPException(status_code=404, detail="Pirate holding not found")
        return _holding_discovery_payload(holding)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get pirate holding id=%s", holding_id)
        raise route_internal_error(
            ERR_PIRATE_HOLDINGS_GET_FAILED,
            "Failed to get pirate holding",
        )


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


@router.post("/{holding_id}/raid/capture")
async def capture_pirate_holding_raid(
    holding_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Complete a pirate-holding raid by capturing the holding.

    Calls capture_holding() atomically (with_for_update row lock + PirateKillLog
    insert in one savepoint per ADR-0060 G-V1). Player must hold the active
    combat lock or be in its team snapshot.
    """
    try:
        holding_uuid = _uuid.UUID(str(holding_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Pirate holding not found")

    player = _load_player_with_team(db, current_player.id)
    try:
        holding = (
            db.query(PirateHolding)
            .filter(PirateHolding.id == holding_uuid)
            .with_for_update()
            .first()
        )
        if holding is None:
            raise HTTPException(status_code=404, detail="Pirate holding not found")

        if holding.owner_team_id is not None or holding.owner_player_id is not None:
            raise HTTPException(status_code=409, detail="Holding is already captured")

        if holding.combat_lock_held_by is None:
            raise HTTPException(status_code=409, detail="No active combat lock on this holding")

        snapshot = holding.combat_lock_team_snapshot or []
        if holding.combat_lock_held_by != player.id and player.id not in snapshot:
            raise HTTPException(status_code=409, detail="Combat lock is held by another player")

        team_id = player.team.id if player.team else player.team_id
        kill_log_entry_kwargs = {
            "region_id": holding.region_id,
            "region_id_snapshot": holding.region_id,
            "holding_id": holding.id,
            "tier": holding.tier,
            "kill_weight": TIER_WEIGHT[holding.tier],
            "attacker_player_id": player.id,
            "attacker_team_id": team_id,
        }
        pes.capture_holding(db, holding, player, kill_log_entry_kwargs=kill_log_entry_kwargs)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to capture pirate holding id=%s", holding_id)
        raise route_internal_error(
            ERR_PIRATE_HOLDINGS_CAPTURE_FAILED,
            "Failed to capture pirate holding",
        )
    return {
        "holding_id": str(holding.id),
        "captured_at": holding.captured_at.isoformat() if holding.captured_at else None,
        "owner_player_id": str(holding.owner_player_id) if holding.owner_player_id else None,
        "owner_team_id": str(holding.owner_team_id) if holding.owner_team_id else None,
    }
