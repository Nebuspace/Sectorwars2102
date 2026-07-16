"""Intra-system helm — authoritative burn / halt / pose (WO-ISP).

Burn cost: FREE (0 turns). Empty-space Travel To allowed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import intrasystem_movement_service as isp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/helm/intrasystem", tags=["intrasystem"])


class BurnRequest(BaseModel):
    x_pct: float = Field(..., ge=0, le=100)
    y_pct: float = Field(..., ge=0, le=100)
    target_kind: Optional[str] = None  # planet | station | point
    target_id: Optional[str] = None


class PoseResponse(BaseModel):
    server_time: str
    x_pct: float
    y_pct: float
    heading_deg: float
    phase: str
    burning: bool
    leg: Optional[Dict[str, Any]] = None
    profile: Dict[str, Any]


def _ship_key(player: Player) -> str:
    return str(player.current_ship_id or player.id)


def _require_in_flight(player: Player) -> None:
    if player.is_docked or player.is_landed:
        raise HTTPException(status_code=400, detail="Cannot burn while docked or landed")


@router.get("/pose", response_model=PoseResponse)
async def get_pose(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    pose = isp.ensure_player_pose(player, _ship_key(player))
    # Materialize completed legs so reload stays clean
    sample = isp.derive_pose(pose)
    if pose.get("leg") and sample.get("phase") == "idle" and sample.get("leg") is None:
        pose = {
            "x_pct": sample["x_pct"],
            "y_pct": sample["y_pct"],
            "heading_deg": sample["heading_deg"],
            "phase": "idle",
            "burning": False,
            "leg": None,
        }
        isp.set_player_pose(db, player, pose)
        db.commit()
    pub = isp.pose_public(player.intrasystem_pose)
    return PoseResponse(**pub)


@router.post("/burn", response_model=PoseResponse)
async def burn(
    body: BurnRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    _require_in_flight(player)
    key = _ship_key(player)
    pose = isp.ensure_player_pose(player, key)
    new_pose = isp.start_burn(
        pose,
        to_x=body.x_pct,
        to_y=body.y_pct,
        sector_id=int(player.current_sector_id),
        ship_key=key,
        target_kind=body.target_kind or "point",
        target_id=body.target_id,
    )
    isp.set_player_pose(db, player, new_pose)
    db.commit()
    ship_id = str(player.current_ship_id) if player.current_ship_id else str(player.id)
    isp.emit_leg_started(int(player.current_sector_id), ship_id, False, new_pose)
    return PoseResponse(**isp.pose_public(new_pose))


@router.post("/halt", response_model=PoseResponse)
async def halt(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    _require_in_flight(player)
    key = _ship_key(player)
    pose = isp.ensure_player_pose(player, key)
    new_pose = isp.start_halt(
        pose,
        sector_id=int(player.current_sector_id),
        ship_key=key,
    )
    isp.set_player_pose(db, player, new_pose)
    db.commit()
    ship_id = str(player.current_ship_id) if player.current_ship_id else str(player.id)
    try:
        import asyncio

        from src.services.websocket_service import connection_manager

        frame = {
            "type": "intrasystem.leg_halted",
            "sector_id": int(player.current_sector_id),
            "ship_id": ship_id,
            "is_npc": False,
            **isp.pose_public(new_pose),
        }
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(
                connection_manager.broadcast_to_sector(int(player.current_sector_id), frame)
            )
    except Exception:
        logger.debug(
            "Skipped intrasystem.leg_halted WS event (no loop or socket)",
            exc_info=True,
        )
    return PoseResponse(**isp.pose_public(new_pose))
