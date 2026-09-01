"""Admin subscription mutations — manual GC grant/revoke (LEG-3611 / ADR-0115)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.admin_scopes import SUBSCRIPTIONS_MODIFY
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.player import Player
from src.models.user import User
from src.services.admin_action_attempt import admin_action_attempt
from src.services.galactic_citizen_admin_service import (
    manual_grant_galactic_citizen,
    manual_revoke_galactic_citizen,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin-subscriptions"])


class GcMutationRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class GcMutationResponse(BaseModel):
    player_id: str
    is_galactic_citizen: bool
    subscription_tier: str | None
    changed: bool
    idempotent: bool
    message: str


def _response_from_player(
    player: Player,
    *,
    changed: bool,
    idempotent: bool,
    message: str,
) -> GcMutationResponse:
    tier: str | None = None
    if player.user is not None:
        tier = player.user.subscription_tier
    return GcMutationResponse(
        player_id=str(player.id),
        is_galactic_citizen=bool(player.is_galactic_citizen),
        subscription_tier=tier,
        changed=changed,
        idempotent=idempotent,
        message=message,
    )


@router.post(
    "/players/{player_id}/galactic-citizen/grant",
    response_model=GcMutationResponse,
)
async def grant_galactic_citizen(
    player_id: str,
    body: GcMutationRequest,
    actor: User = Depends(require_scope(SUBSCRIPTIONS_MODIFY)),
    db: Session = Depends(get_db),
):
    """Manual GC grant (comp) — gated by admin.subscriptions.modify."""
    try:
        with admin_action_attempt(
            db,
            actor=actor,
            scope_used=SUBSCRIPTIONS_MODIFY,
            action="galactic_citizen_grant",
            target_type="player",
            target_id=str(player_id),
            payload={"reason": body.reason, "source": "admin_manual"},
        ) as attempt:
            player = db.query(Player).filter(Player.id == player_id).first()
            if player is None:
                raise HTTPException(status_code=404, detail="Player not found")

            try:
                outcome = manual_grant_galactic_citizen(
                    db, player, reason=body.reason
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            attempt.succeed(payload=outcome.details)
            db.refresh(player)
            if player.user_id:
                user = db.query(User).filter(User.id == player.user_id).first()
                if user is not None:
                    player.user = user

            message = (
                "Galactic citizenship already active"
                if outcome.already_in_target_state
                else "Galactic citizenship granted"
            )
            return _response_from_player(
                player,
                changed=outcome.changed,
                idempotent=outcome.already_in_target_state,
                message=message,
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to grant galactic citizenship")
        raise HTTPException(
            status_code=500, detail="Failed to grant galactic citizenship"
        )


@router.post(
    "/players/{player_id}/galactic-citizen/revoke",
    response_model=GcMutationResponse,
)
async def revoke_galactic_citizen(
    player_id: str,
    body: GcMutationRequest,
    actor: User = Depends(require_scope(SUBSCRIPTIONS_MODIFY)),
    db: Session = Depends(get_db),
):
    """Manual GC revoke (clawback) — gated by admin.subscriptions.modify."""
    try:
        with admin_action_attempt(
            db,
            actor=actor,
            scope_used=SUBSCRIPTIONS_MODIFY,
            action="galactic_citizen_revoke",
            target_type="player",
            target_id=str(player_id),
            payload={"reason": body.reason, "source": "admin_manual"},
        ) as attempt:
            player = db.query(Player).filter(Player.id == player_id).first()
            if player is None:
                raise HTTPException(status_code=404, detail="Player not found")

            try:
                outcome = manual_revoke_galactic_citizen(
                    db, player, reason=body.reason
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            attempt.succeed(payload=outcome.details)
            db.refresh(player)
            if player.user_id:
                user = db.query(User).filter(User.id == player.user_id).first()
                if user is not None:
                    player.user = user

            message = (
                "Galactic citizenship already absent"
                if outcome.already_in_target_state
                else "Galactic citizenship revoked"
            )
            return _response_from_player(
                player,
                changed=outcome.changed,
                idempotent=outcome.already_in_target_state,
                message=message,
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to revoke galactic citizenship")
        raise HTTPException(
            status_code=500, detail="Failed to revoke galactic citizenship"
        )
