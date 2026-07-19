"""
Pirate-ecosystem read route: region-scoped population/target/cleansed-state
snapshot for the pirate holding ecosystem (WO-PIRATE-ECO-1 lane D).

Canon reference: SYSTEMS/pirate-ecosystem.md (sw2102-docs) -- source map
(:434-449) names this route ``GET /api/v1/regions/{region_id}/pirate-
ecosystem``. Population score, target, and cleansed state all come from
``pirate_ecosystem_service.refresh_pirate_ecosystem_snapshot`` -- the same
snapshot shape the eventual weekly tick writes (pirate-ecosystem.md:379-
399's "fast-path read cache"). The holdings summary (counts per tier, not
full rows) queries ``PirateHolding`` directly.

Service contract (mirrors routes/station_security.py): this router codes
to the ``pirate_ecosystem_service`` module surface. The route lazily
refreshes the snapshot on read (mirrors station_security.
get_security_status's "lazy-settle, public read" idiom), then commits --
``refresh_pirate_ecosystem_snapshot`` only flushes per its own docstring;
the route owns the commit.

World-state read: any authenticated player, no admin/ownership gate.
"""
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.pirate_holding import PirateHolding
from src.models.player import Player
from src.models.region import Region
from src.models.user import User
from src.services import pirate_ecosystem_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regions", tags=["pirate-ecosystem"])


def _get_region_or_404(db: Session, region_id: str) -> Region:
    """Fetch a region by id, turning malformed UUIDs into a 404 instead of
    a DataError that surfaces as a generic 'Database error occurred' 500
    (matches routes/station_security.py's identical helper)."""
    try:
        region_uuid = _uuid.UUID(str(region_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Region not found")
    region = db.query(Region).filter(Region.id == region_uuid).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    return region


_TIER_SUMMARY_DEFAULT = {"camp": 0, "outpost": 0, "stronghold": 0}


def _holdings_by_tier(db: Session, region_id: _uuid.UUID) -> dict:
    """Counts of pirate-controlled (uncaptured) holdings per tier -- NOT
    full holding rows, per WO-PIRATE-ECO-1 lane D scope. Mirrors
    pirate_ecosystem_service.score_holdings's own owner_player_id IS NULL
    filter (pirate-ecosystem.md:59's "not player-captured" exclusion)."""
    rows = (
        db.query(PirateHolding.tier, func.count(PirateHolding.id))
        .filter(
            PirateHolding.region_id == region_id,
            PirateHolding.owner_player_id.is_(None),
        )
        .group_by(PirateHolding.tier)
        .all()
    )
    summary = dict(_TIER_SUMMARY_DEFAULT)
    for tier, count in rows:
        key = tier.value.lower()
        summary[key] = summary.get(key, 0) + count
    return summary


@router.get("/{region_id}/pirate-ecosystem")
async def get_region_pirate_ecosystem(
    region_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_player: Player = Depends(get_current_player),
):
    """Region-scoped pirate-ecosystem snapshot: live population score,
    growth target, cleansed-state, and per-tier holdings counts. World
    state -- any authenticated player may read any region (no ownership/
    admin gate); mirrors station_security's public-read idiom."""
    region = _get_region_or_404(db, region_id)

    state = pirate_ecosystem_service.refresh_pirate_ecosystem_snapshot(db, region)
    db.commit()

    holdings = _holdings_by_tier(db, region.id)

    return {
        "region_id": str(region.id),
        "population_score": state.get("current_population_score"),
        "target_population": state.get("current_target"),
        "suppression_modifier": state.get("suppression_modifier"),
        "kill_weight_last_30_days": state.get("kill_weight_last_30_days"),
        "cleansed_at": state.get("cleansed_at"),
        "zero_population_since": state.get("zero_population_since"),
        "holdings_by_tier": holdings,
    }
