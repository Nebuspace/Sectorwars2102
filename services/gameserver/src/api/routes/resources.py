"""
Resource Registry API routes (WO-ARCH-RES-1-KERNEL).

Read-only catalog of the seeded resources (definitions.md#resource-types
plus precious_metals per ADR-0062 E-D1 / mining.md), seeded idempotently at
startup by src.core.resource_registry_seeder. A new resource inserted into
the registry surfaces here with zero code change.

Auth: any authenticated user (player or admin-only). The catalog is not
player-scoped — requiring a Player row wrongly 404'd default admin sessions
that have User + AdminCredentials but no Player (WO-ADM-RESOURCE-CATALOG-AUTH).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.models.resource import Resource
from src.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


class ResourceOut(BaseModel):
    name: str
    label: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None
    base_price: Optional[int] = None
    price_range_min: Optional[int] = None
    price_range_max: Optional[int] = None
    is_storable: bool
    is_producible: bool

    class Config:
        from_attributes = True


@router.get("", response_model=List[ResourceOut])
async def list_resources(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the seeded resource catalog, ordered as declared in the registry.

    ``current_user`` authenticates the caller; the query itself is global and
    does not filter by user/player.
    """
    _ = current_user  # auth gate only — catalog is not per-user
    resources = (
        db.query(Resource)
        .filter(Resource.is_active.is_(True))
        .order_by(Resource.category, Resource.name)
        .all()
    )
    return resources
