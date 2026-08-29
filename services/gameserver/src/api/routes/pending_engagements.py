"""Player GET for open PendingEngagement rows (LEG-480 / police-forces.md:49).

Reconnect contract: durable PENDING rows for the authenticated player only.
Empty → 204. Live countdown also emits as WS type ``police_en_route`` at
route_engagement commit (combat + movement-path).
"""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.models.player import Player
from src.services import npc_engagement_service

router = APIRouter(prefix="/pending-engagements", tags=["pending-engagements"])


@router.get("")
async def get_my_pending_engagements(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Return the caller's PENDING police-en-route rows, or 204 when none."""
    items = npc_engagement_service.list_open_engagement_summaries(db, player)
    if not items:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return {"items": items}
