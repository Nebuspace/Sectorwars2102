"""
Resource Registry API routes (WO-ARCH-RES-1-KERNEL).

Read-only catalog of the 13 canon resources (definitions.md#resource-types),
seeded idempotently at startup by src.core.resource_registry_seeder. A new
resource inserted into the registry surfaces here with zero code change.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.models.resource import Resource

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
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Return the seeded resource catalog, ordered as declared in the registry."""
    resources = (
        db.query(Resource)
        .filter(Resource.is_active.is_(True))
        .order_by(Resource.category, Resource.name)
        .all()
    )
    return resources
