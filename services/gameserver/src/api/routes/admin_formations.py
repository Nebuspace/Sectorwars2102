"""Admin special-formation operator tools (LEG-52 Gold Bubble placement).

Canon source map names ``place_gold_bubble``; this module hosts that route
rather than further bloating ``admin.py`` (WO-LEG-52 allows a dedicated
admin-formations route file). Scope: ``admin.galaxy.manage``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.admin_scopes import GALAXY_MANAGE
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.user import User
from src.services.admin_action_attempt import admin_action_attempt
from src.services.special_formation_service import (
    GOLD_BUBBLE_INTERIOR_SIZE_MIN,
    GoldBubblePlacementError,
    place_gold_bubble,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PlaceGoldBubbleRequest(BaseModel):
    """Operator hand-placement payload for GOLD_BUBBLE.

    Admin UI (LEG-184) must consume this schema without inventing fields.
    """

    gateway_sector_ids: List[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="1–3 gateway (articulation) sector UUIDs; first is primary anchor.",
    )
    interior_sector_ids: List[uuid.UUID] = Field(
        ...,
        min_length=GOLD_BUBBLE_INTERIOR_SIZE_MIN,
        description=f">= {GOLD_BUBBLE_INTERIOR_SIZE_MIN} interior sector UUIDs.",
    )
    name: Optional[str] = Field(None, max_length=100)
    discovery_requirement: Optional[Dict[str, Any]] = None
    isolate_warps: bool = Field(
        True,
        description=(
            "Phase B: strip warps that violate the bubble envelope before "
            "writing the formation row (default True)."
        ),
    )


def _map_gold_bubble_error(exc: GoldBubblePlacementError) -> HTTPException:
    if exc.code in ("region_not_found", "sector_not_in_region"):
        return HTTPException(status_code=404, detail=exc.detail)
    if exc.code in ("formation_overlap",):
        return HTTPException(status_code=409, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


@router.post(
    "/regions/{region_id}/formations/gold-bubble",
    response_model=dict,
    summary="place_gold_bubble — operator Gold Bubble stamp",
)
async def place_gold_bubble_route(
    region_id: uuid.UUID,
    body: PlaceGoldBubbleRequest,
    current_admin: User = Depends(require_scope(GALAXY_MANAGE)),
    db: Session = Depends(get_db),
):
    """Stamp a GOLD_BUBBLE into a live region (operator-only; not random budget).

    Function name ``place_gold_bubble`` is the design-target symbol cited by
    ``SYSTEMS/special-formations-generation.md`` source map.
    """
    with admin_action_attempt(
        db,
        actor=current_admin,
        scope_used=GALAXY_MANAGE,
        action="place_gold_bubble",
        target_type="special_formation",
        target_id="pending",
        payload={
            "region_id": str(region_id),
            "gateway_count": len(body.gateway_sector_ids),
            "interior_size": len(body.interior_sector_ids),
            "isolate_warps": body.isolate_warps,
            "name": body.name,
        },
    ) as attempt:
        try:
            formation = place_gold_bubble(
                db,
                region_id=region_id,
                gateway_sector_ids=body.gateway_sector_ids,
                interior_sector_ids=body.interior_sector_ids,
                name=body.name,
                discovery_requirement=body.discovery_requirement,
                isolate_warps=body.isolate_warps,
            )
            attempt.target_id = str(formation.id)
            attempt.succeed(
                payload={
                    "region_id": str(region_id),
                    "formation_id": str(formation.id),
                    "gateway_count": len(body.gateway_sector_ids),
                    "interior_size": len(formation.interior_sector_ids or []),
                    "isolate_warps": body.isolate_warps,
                    "name": formation.name,
                }
            )
            return {
                "success": True,
                "formation": {
                    "id": str(formation.id),
                    "type": formation.type.value,
                    "name": formation.name,
                    "region_id": str(formation.region_id),
                    "anchor_sector_id": str(formation.anchor_sector_id),
                    "interior_sector_ids": [
                        str(s) for s in (formation.interior_sector_ids or [])
                    ],
                    "properties": formation.properties or {},
                    "discovery_requirement": formation.discovery_requirement,
                },
            }
        except GoldBubblePlacementError as exc:
            raise _map_gold_bubble_error(exc) from exc
        except HTTPException:
            raise
        except Exception as e:
            logger.error("place_gold_bubble failed for region %s: %s", region_id, e)
            raise HTTPException(
                status_code=500,
                detail="Failed to place Gold Bubble",
            ) from e


# Alias matching the canon design-target symbol for greppability / source map.
place_gold_bubble = place_gold_bubble_route
