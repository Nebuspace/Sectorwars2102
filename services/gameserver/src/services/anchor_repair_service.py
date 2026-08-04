"""Anchor-repair daily scan — detect-only thin v1 (WO-ANCHOR-REPAIR-SERVICE).

Canon: SYSTEMS/anchor-repair-service.md (Design-only full WR12). This module
ships the existence-check + ``region_anchor_missing`` event kernel only.
Re-injection via Phase-11 placement helpers is explicitly deferred — there is
no ``GalaxyGenerator._place_phase_11_anchors`` to reuse yet.

Cadence host: rides the governance sweep's once-per-canonical-day Phase 8
(alongside region-lifecycle WR9), not a separate cron file.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from src.models.planet import Planet, PlanetType
from src.models.region import Region, RegionStatus, RegionType
from src.models.sector import Sector
from src.models.station import Station, StationClass, StationStatus

logger = logging.getLogger(__name__)

# Hub GO (b): role vocabulary on Station.region_assignment_role.
ROLE_STARTER = "starter"
ROLE_FRONTIER = "frontier"

ANCHOR_CAPITAL_TERRA = "capital_terra"
ANCHOR_CLASS1_COMMERCE = "class1_commerce"
ANCHOR_SPACEDOCK_STARTER = "spacedock_starter"
ANCHOR_SPACEDOCK_FRONTIER = "spacedock_frontier"

# Present if the station is still "in the world" — abandoned does not count.
_STATION_PRESENT_STATUSES = {
    StationStatus.OPERATIONAL,
    StationStatus.DAMAGED,
    StationStatus.UNDER_CONSTRUCTION,
    StationStatus.UNDER_ATTACK,
    StationStatus.LOCKDOWN,
    StationStatus.RESTRICTED,
}


def _region_capital_global_id(region: Region, sids: Sequence[int]) -> Optional[int]:
    """Map region-local capital_sector_number onto a global sector_id.

    Same arithmetic as npc_movement_service._region_capital_global_id — local 1
    is the first sector in the ascending sector_id list.
    """
    if not sids:
        return None
    ordered = sorted(int(s) for s in sids)
    local = int(region.capital_sector_number or 1)
    candidate = ordered[0] + local - 1
    if candidate in ordered:
        return candidate
    return ordered[0]


def _starter_cluster_global_sids(
    sids: Sequence[int], capital_global: int,
) -> List[int]:
    """Capital + next sectors in sorted global order (Class-1 commerce band).

    Canon prefers capital_sector_number+1 with starter-cluster fallback. Without
    a zone map we take the capital and the following slice of the region's
    sorted sector_ids (at least capital+1 when present).
    """
    ordered = sorted(int(s) for s in sids)
    if not ordered:
        return []
    if capital_global not in ordered:
        return ordered[: max(2, len(ordered) // 10)]
    idx = ordered.index(capital_global)
    window = max(2, min(len(ordered) - idx, max(10, len(ordered) // 10)))
    return ordered[idx : idx + window]


def region_is_scannable(region: Region) -> bool:
    """Active non-nexus regions only — matches canon skip set."""
    if region.region_type == RegionType.CENTRAL_NEXUS:
        return False
    status = region.status
    if isinstance(status, RegionStatus):
        return status == RegionStatus.ACTIVE
    return str(status) == RegionStatus.ACTIVE.value


def check_capital_terra(
    db: Session, region: Region, capital_global: Optional[int],
) -> str:
    """Return present|missing. Canon TERRA → PlanetType.TERRAN in code."""
    if capital_global is None:
        return "missing"
    found = (
        db.query(Planet.id)
        .filter(
            Planet.region_id == region.id,
            Planet.type == PlanetType.TERRAN,
            Planet.sector_id == capital_global,
        )
        .first()
    )
    return "present" if found else "missing"


def check_class1_commerce(
    db: Session,
    region: Region,
    starter_sids: Sequence[int],
) -> str:
    """CLASS_1 station in the starter-cluster sector band (enum CLASS_1)."""
    if not starter_sids:
        return "missing"
    found = (
        db.query(Station.id)
        .filter(
            Station.region_id == region.id,
            Station.station_class == StationClass.CLASS_1,
            Station.sector_id.in_(list(starter_sids)),
            Station.status.in_(list(_STATION_PRESENT_STATUSES)),
        )
        .first()
    )
    return "present" if found else "missing"


def check_spacedock_role(
    db: Session, region: Region, role: str,
) -> str:
    """present|missing|unverifiable for a SpaceDock with an explicit role.

    Unset roles across the region's SpaceDocks → unverifiable (hub GO b:
    do not false-fail).
    """
    spacedocks = (
        db.query(Station)
        .filter(
            Station.region_id == region.id,
            Station.is_spacedock == True,  # noqa: E712 — SQLAlchemy boolean col
            Station.status.in_(list(_STATION_PRESENT_STATUSES)),
        )
        .all()
    )
    if not spacedocks:
        # No SpaceDock at all — missing for this role (can't be unverifiable
        # without any candidate; ops still need a signal).
        return "missing"
    roles_seen = {getattr(s, "region_assignment_role", None) for s in spacedocks}
    if role not in roles_seen and all(r is None for r in roles_seen):
        return "unverifiable"
    for station in spacedocks:
        if getattr(station, "region_assignment_role", None) == role:
            return "present"
    return "missing"


def _missing_event(
    region: Region, anchor_type: str, sector_id: Optional[int],
) -> Dict[str, Any]:
    return {
        "type": "region_anchor_missing",
        "region_id": str(region.id),
        "region_name": getattr(region, "name", None),
        "anchor_type": anchor_type,
        "sector_id": sector_id,
    }


def scan_region(db: Session, region: Region) -> Dict[str, Any]:
    """Run the four existence checks for one region. Detect-only."""
    sids = [
        sid
        for (sid,) in db.query(Sector.sector_id)
        .filter(Sector.region_id == region.id)
        .all()
    ]
    capital = _region_capital_global_id(region, sids)
    starter = _starter_cluster_global_sids(sids, capital) if capital is not None else []

    results = {
        ANCHOR_CAPITAL_TERRA: check_capital_terra(db, region, capital),
        ANCHOR_CLASS1_COMMERCE: check_class1_commerce(db, region, starter),
        ANCHOR_SPACEDOCK_STARTER: check_spacedock_role(db, region, ROLE_STARTER),
        ANCHOR_SPACEDOCK_FRONTIER: check_spacedock_role(db, region, ROLE_FRONTIER),
    }
    events: List[Dict[str, Any]] = []
    for anchor_type, outcome in results.items():
        if outcome == "missing":
            events.append(_missing_event(region, anchor_type, capital))
            logger.warning(
                "anchor_repair: region_anchor_missing region=%s name=%s anchor=%s",
                region.id,
                getattr(region, "name", None),
                anchor_type,
            )
    return {"checks": results, "events": events}


def run_daily_scan(db: Session) -> Dict[str, Any]:
    """Scan every active non-nexus region. Flush-only; caller owns commit.

    Returns ``{regions_scanned, missing_count, unverifiable_count, events}``.
    """
    regions = db.query(Region).all()
    events: List[Dict[str, Any]] = []
    scanned = 0
    missing = 0
    unverifiable = 0
    for region in regions:
        if not region_is_scannable(region):
            continue
        scanned += 1
        outcome = scan_region(db, region)
        events.extend(outcome["events"])
        for value in outcome["checks"].values():
            if value == "missing":
                missing += 1
            elif value == "unverifiable":
                unverifiable += 1
    return {
        "regions_scanned": scanned,
        "missing_count": missing,
        "unverifiable_count": unverifiable,
        "events": events,
    }


def run_daily_scan_gated(db: Session) -> Dict[str, Any]:
    """Once-per-canonical-day gate around ``run_daily_scan``.

    Mirrors ``_run_region_lifecycle_advance_gated``'s Galaxy.state day-anchor
    discipline. Intended to ride governance Phase 8 under the shared
    governance advisory lock (caller owns commit).

    Returns {anchor_repair_skipped, anchors_scanned, anchors_missing,
    unverifiable_count, events}.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from src.models.galaxy import Galaxy
    from src.services.scheduler._common import (
        _ANCHOR_REPAIR_STATE_KEY,
        canonical_day_number,
    )

    result: Dict[str, Any] = {
        "anchor_repair_skipped": False,
        "anchors_scanned": 0,
        "anchors_missing": 0,
        "unverifiable_count": 0,
        "events": [],
    }

    this_day = canonical_day_number()
    galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
    gstate = dict(galaxy.state or {}) if galaxy is not None else {}
    last_day = gstate.get(_ANCHOR_REPAIR_STATE_KEY)
    already_today = (
        galaxy is not None
        and last_day is not None
        and int(last_day) >= this_day
    )
    if already_today:
        result["anchor_repair_skipped"] = True
        return result

    scan = run_daily_scan(db)
    result["anchors_scanned"] = scan["regions_scanned"]
    result["anchors_missing"] = scan["missing_count"]
    result["unverifiable_count"] = scan["unverifiable_count"]
    result["events"] = list(scan.get("events") or [])

    if galaxy is not None:
        gstate = dict(galaxy.state or {})
        gstate[_ANCHOR_REPAIR_STATE_KEY] = this_day
        galaxy.state = gstate
        flag_modified(galaxy, "state")
    return result
