"""Admin player bulk operations (LEG-903)."""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth.admin_scopes import (
    PLAYERS_ADJUST_CREDITS,
    PLAYERS_ADJUST_REP,
    PLAYERS_SUSPEND,
    PLAYERS_VIEW,
)
from src.auth.dependencies import require_scope, user_has_active_scope
from src.core.database import get_db
from src.models.faction import Faction
from src.models.player import Player
from src.models.reputation import Reputation
from src.models.user import User
from src.services.admin_action_log_service import log_admin_action
from src.services.faction_service import FactionService

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_OPERATIONS = frozenset(
    {"CREDIT_ADJUST", "TURN_GRANT", "STATUS_CHANGE", "REPUTATION_ADJUST"}
)
ALLOWED_STATUSES = frozenset({"active", "inactive", "banned", "suspended"})

_OPERATION_SCOPE: dict[str, str] = {
    "CREDIT_ADJUST": PLAYERS_ADJUST_CREDITS,
    "TURN_GRANT": PLAYERS_ADJUST_CREDITS,
    "STATUS_CHANGE": PLAYERS_SUSPEND,
    "REPUTATION_ADJUST": PLAYERS_ADJUST_REP,
}


class ReputationChangeItem(BaseModel):
    faction: str
    new_value: int


class BulkOperationParameters(BaseModel):
    amount: Optional[int] = None
    new_status: Optional[str] = None
    reputation_changes: Optional[list[ReputationChangeItem]] = None
    reason: str = Field(..., min_length=1)


class BulkOperationRequest(BaseModel):
    player_ids: list[str] = Field(..., min_length=1)
    operation: Literal[
        "CREDIT_ADJUST", "TURN_GRANT", "STATUS_CHANGE", "REPUTATION_ADJUST"
    ]
    parameters: BulkOperationParameters


class BulkOperationItemResult(BaseModel):
    player_id: str
    success: bool
    detail: Optional[str] = None


class BulkOperationResponse(BaseModel):
    operation: str
    applied: int
    rejected: int
    results: list[BulkOperationItemResult]


def _validate_parameters(request: BulkOperationRequest) -> None:
    op = request.operation
    params = request.parameters
    if not params.reason.strip():
        raise HTTPException(status_code=400, detail="parameters.reason is required")
    if op in ("CREDIT_ADJUST", "TURN_GRANT"):
        if params.amount is None:
            raise HTTPException(
                status_code=400,
                detail=f"parameters.amount is required for {op}",
            )
    elif op == "STATUS_CHANGE":
        if not params.new_status:
            raise HTTPException(
                status_code=400,
                detail="parameters.new_status is required for STATUS_CHANGE",
            )
        if params.new_status not in ALLOWED_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid new_status. Must be one of: "
                    + ", ".join(sorted(ALLOWED_STATUSES))
                ),
            )
    elif op == "REPUTATION_ADJUST":
        if not params.reputation_changes:
            raise HTTPException(
                status_code=400,
                detail="parameters.reputation_changes is required for REPUTATION_ADJUST",
            )


def _apply_status(player: Player, new_status: str) -> None:
    player.is_active = new_status == "active"


async def _apply_reputation_changes(
    db: Session,
    *,
    player: Player,
    changes: list[ReputationChangeItem],
    reason: str,
    admin_username: str,
) -> list[str]:
    """Apply absolute reputation targets; return list of per-faction error strings."""
    errors: list[str] = []
    faction_service = FactionService(db)
    for item in changes:
        faction_row = (
            db.query(Faction)
            .filter(func.lower(Faction.name) == item.faction.lower())
            .first()
        )
        if not faction_row:
            errors.append(f"faction_not_found:{item.faction}")
            continue
        current = (
            db.query(Reputation)
            .filter(
                Reputation.player_id == player.id,
                Reputation.faction_id == faction_row.id,
            )
            .first()
        )
        current_value = current.current_value if current else 0
        delta = item.new_value - current_value
        if delta == 0:
            continue
        await faction_service.update_reputation(
            player_id=player.id,
            faction_id=faction_row.id,
            change=delta,
            reason=f"{reason} (bulk by {admin_username})",
        )
    return errors


@router.post("/players/bulk-operation", response_model=BulkOperationResponse)
async def bulk_player_operation(
    request: BulkOperationRequest,
    admin: User = Depends(require_scope(PLAYERS_VIEW)),
    db: Session = Depends(get_db),
) -> BulkOperationResponse:
    """Mass player admin operations (OPERATIONS/admin-ui.md bulk tier).

    Path contains ``/bulk`` so ``classify_admin_tier`` assigns ADMIN_TIER_BULK
    (10/min). Per-player soft failure — invalid/missing ids reported in
    ``results`` without aborting siblings.
    """
    if request.operation not in ALLOWED_OPERATIONS:
        raise HTTPException(status_code=400, detail="Invalid operation")

    _validate_parameters(request)

    required_scope = _OPERATION_SCOPE[request.operation]
    if not user_has_active_scope(db, admin.id, required_scope):
        raise HTTPException(
            status_code=403,
            detail=f"Missing required scope: {required_scope}",
        )

    try:
        results: list[BulkOperationItemResult] = []
        applied = 0
        rejected = 0

        for raw_id in request.player_ids:
            try:
                player_uuid = uuid.UUID(str(raw_id))
            except (ValueError, TypeError):
                rejected += 1
                results.append(
                    BulkOperationItemResult(
                        player_id=str(raw_id),
                        success=False,
                        detail="invalid_player_id",
                    )
                )
                continue

            try:
                player = db.query(Player).filter(Player.id == player_uuid).first()
                if not player:
                    rejected += 1
                    results.append(
                        BulkOperationItemResult(
                            player_id=str(raw_id),
                            success=False,
                            detail="player_not_found",
                        )
                    )
                    continue

                params = request.parameters
                if request.operation == "CREDIT_ADJUST":
                    new_credits = player.credits + int(params.amount)
                    if new_credits < 0:
                        rejected += 1
                        results.append(
                            BulkOperationItemResult(
                                player_id=str(raw_id),
                                success=False,
                                detail="credits_would_be_negative",
                            )
                        )
                        continue
                    player.credits = new_credits
                elif request.operation == "TURN_GRANT":
                    new_turns = player.turns + int(params.amount)
                    if new_turns < 0:
                        rejected += 1
                        results.append(
                            BulkOperationItemResult(
                                player_id=str(raw_id),
                                success=False,
                                detail="turns_would_be_negative",
                            )
                        )
                        continue
                    player.turns = new_turns
                elif request.operation == "STATUS_CHANGE":
                    _apply_status(player, params.new_status)
                elif request.operation == "REPUTATION_ADJUST":
                    rep_errors = await _apply_reputation_changes(
                        db,
                        player=player,
                        changes=params.reputation_changes or [],
                        reason=params.reason,
                        admin_username=admin.username,
                    )
                    if rep_errors:
                        rejected += 1
                        results.append(
                            BulkOperationItemResult(
                                player_id=str(raw_id),
                                success=False,
                                detail=";".join(rep_errors),
                            )
                        )
                        continue

                applied += 1
                results.append(
                    BulkOperationItemResult(player_id=str(raw_id), success=True)
                )
            except Exception as exc:  # noqa: BLE001 — per-id soft fail
                rejected += 1
                results.append(
                    BulkOperationItemResult(
                        player_id=str(raw_id),
                        success=False,
                        detail=str(exc)[:200],
                    )
                )

        if applied:
            log_admin_action(
                db,
                actor=admin,
                scope_used=required_scope,
                action="players_bulk_operation",
                target_type="player",
                target_id="bulk",
                payload={
                    "operation": request.operation,
                    "player_count": len(request.player_ids),
                    "applied": applied,
                    "rejected": rejected,
                    "reason": request.parameters.reason,
                },
            )
            db.commit()

        return BulkOperationResponse(
            operation=request.operation,
            applied=applied,
            rejected=rejected,
            results=results,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error in bulk_player_operation")
        raise HTTPException(status_code=500, detail="Bulk player operation failed")
