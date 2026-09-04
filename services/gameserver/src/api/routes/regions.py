"""Region lifecycle routes (LEG-3764).

Canon target: ``POST /api/v1/regions/{region_id}/takeover`` —
``SYSTEMS/region-lifecycle.md`` § Takeover endpoint.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_auth
from src.core.config import settings
from src.core.database import get_async_session
from src.models.user import User
from src.services.region_lifecycle_service import (
    ERR_GALACTIC_CITIZEN_REQUIRED,
    ERR_ONE_REGION_PER_OWNER,
    ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER,
    ERR_REGION_NOT_FOUND,
    ERR_TAKEOVER_INTENT_PENDING,
    execute_takeover,
    list_takeover_eligible_regions,
)
from src.utils.error_handling import route_internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regions", tags=["regions"])

ERR_REGIONS_TAKEOVER_ELIGIBLE_LIST_FAILED = "ERR_REGIONS_TAKEOVER_ELIGIBLE_LIST_FAILED"
ERR_REGIONS_TAKEOVER_BEGIN_FAILED = "ERR_REGIONS_TAKEOVER_BEGIN_FAILED"

_TAKEOVER_ERROR_STATUS = {
    ERR_REGION_NOT_FOUND: 404,
    ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER: 409,
    ERR_GALACTIC_CITIZEN_REQUIRED: 403,
    ERR_ONE_REGION_PER_OWNER: 409,
    ERR_TAKEOVER_INTENT_PENDING: 409,
    "ERR_PAYPAL_APPROVAL_URL_MISSING": 502,
}


class RegionTakeoverRequest(BaseModel):
    """Optional PayPal redirect URLs; defaults derive from FRONTEND_URL."""

    return_url: Optional[str] = Field(
        None,
        description="PayPal success return URL (defaults to FRONTEND_URL/regions/takeover/success)",
    )
    cancel_url: Optional[str] = Field(
        None,
        description="PayPal cancel return URL (defaults to FRONTEND_URL/regions/takeover/cancel)",
    )


def _default_takeover_urls() -> tuple[str, str]:
    base = (settings.FRONTEND_URL or "http://localhost:3000").rstrip("/")
    return f"{base}/regions/takeover/success", f"{base}/regions/takeover/cancel"


@router.get("/takeover-eligible")
async def list_takeover_eligible_regions_route(
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """List suspended/grace regions eligible for GC-subscription takeover."""
    try:
        return await list_takeover_eligible_regions(db)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to list takeover-eligible regions")
        raise route_internal_error(
            ERR_REGIONS_TAKEOVER_ELIGIBLE_LIST_FAILED,
            "Failed to list takeover-eligible regions",
        )


@router.post("/{region_id}/takeover", status_code=201)
async def takeover_region(
    region_id: uuid.UUID,
    body: RegionTakeoverRequest | None = None,
    current_user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_async_session),
):
    """Begin GC-subscription takeover of a suspended or grace-period region."""
    try:
        payload = body or RegionTakeoverRequest()
        return_url, cancel_url = _default_takeover_urls()
        if payload.return_url:
            return_url = payload.return_url
        if payload.cancel_url:
            cancel_url = payload.cancel_url

        result = await execute_takeover(
            db,
            region_id=region_id,
            caller_user_id=current_user.id,
            return_url=return_url,
            cancel_url=cancel_url,
        )
        if not result.get("ok"):
            code = result.get("code", "ERR_TAKEOVER_FAILED")
            raise HTTPException(
                status_code=_TAKEOVER_ERROR_STATUS.get(code, 400),
                detail=code,
            )

        await db.commit()
        return result["takeover_intent"]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to begin region takeover")
        raise route_internal_error(
            ERR_REGIONS_TAKEOVER_BEGIN_FAILED,
            "Failed to begin region takeover",
        )
