"""Anchor-repair daily scan + Phase-11 reinjection (WO-FIX-ANCHOR-REPAIR-ACTIVE-LOOP).

Canon: SYSTEMS/anchor-repair-service.md + galaxy-generator-design.md Phase 11.
Detects missing Capital TERRA / Class-1 / SpaceDock starter|frontier anchors
and reinjects them via placement rules that reproduce Phase-11 (capital,
capital+1, capital+9, total−5 with cluster fallbacks). There is still no
``GalaxyGenerator._place_phase_11_anchors`` — this module *is* that helper.

Cadence host: rides the governance sweep's once-per-canonical-day Phase 8
(alongside region-lifecycle WR9), not a separate cron file.
"""

from __future__ import annotations

import hashlib
import logging
import random
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.market_bootstrap import build_market_prices
from src.core.station_class_map import apply_class_pattern
from src.models.planet import Planet, PlanetStatus, PlanetType
from src.models.region import Region, RegionStatus, RegionType
from src.models.sector import Sector
from src.models.station import Station, StationClass, StationStatus, StationType

logger = logging.getLogger(__name__)

# Distinct from scheduler region locks — repair must not share that key family.
_ANCHOR_REPAIR_LOCK_BASE = 0x41525052  # 'ARPR'
_LOCK_KEY_MASK_63 = (1 << 63) - 1

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

_SPACEDOCK_SERVICES = {
    "ship_dealer": True,
    "ship_repair": True,
    "ship_maintenance": True,
    "ship_upgrades": True,
    "insurance": True,
    "drone_shop": True,
    "genesis_dealer": True,
    "mine_dealer": True,
    "diplomatic_services": False,
    "storage_rental": True,
    "market_intelligence": True,
    "refining_facility": True,
    "luxury_amenities": True,
}


def _region_lock_key(region_id: Any) -> int:
    digest = hashlib.blake2b(
        f"anchor-repair:{region_id}".encode("utf-8"), digest_size=8,
    ).digest()
    return (_ANCHOR_REPAIR_LOCK_BASE ^ int.from_bytes(digest, "big")) & _LOCK_KEY_MASK_63


def _lock_region(db: Session, region_id: Any) -> None:
    """Per-region advisory lock (ADR-0050 SK18). No-op when session lacks execute."""
    execute = getattr(db, "execute", None)
    if execute is None:
        return
    execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _region_lock_key(region_id)},
    )


def _region_sector_ids(db: Session, region: Region) -> List[int]:
    return [
        sid
        for (sid,) in db.query(Sector.sector_id)
        .filter(Sector.region_id == region.id)
        .all()
    ]


def _local_to_global(sids: Sequence[int], local_n: int) -> Optional[int]:
    """Map region-local sector number onto a global sector_id (contiguous band)."""
    ordered = sorted(int(s) for s in sids)
    if not ordered:
        return None
    candidate = ordered[0] + int(local_n) - 1
    if candidate in ordered:
        return candidate
    return None


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


def _occupied_station_sids(db: Session, region_id: Any) -> Set[int]:
    stations = (
        db.query(Station)
        .filter(
            Station.region_id == region_id,
            Station.status.in_(list(_STATION_PRESENT_STATUSES)),
        )
        .all()
    )
    return {int(s.sector_id) for s in stations}


def _sector_row(db: Session, region: Region, sector_id: int) -> Optional[Sector]:
    return (
        db.query(Sector)
        .filter(Sector.region_id == region.id, Sector.sector_id == sector_id)
        .first()
    )


def _pick_free(candidates: Sequence[int], occupied: Set[int]) -> Optional[int]:
    for sid in candidates:
        if sid is not None and int(sid) not in occupied:
            return int(sid)
    return None


def _class1_candidates(
    sids: Sequence[int], capital_global: int, capital_local: int,
) -> List[int]:
    preferred = _local_to_global(sids, capital_local + 1)
    starter = _starter_cluster_global_sids(sids, capital_global)
    ordered: List[int] = []
    if preferred is not None:
        ordered.append(preferred)
    for sid in starter:
        if sid not in ordered:
            ordered.append(sid)
    return ordered


def _starter_dock_candidates(
    sids: Sequence[int], capital_global: int, capital_local: int,
) -> List[int]:
    preferred = _local_to_global(sids, capital_local + 9)
    starter = _starter_cluster_global_sids(sids, capital_global)
    ordered: List[int] = []
    if preferred is not None:
        ordered.append(preferred)
    for sid in starter:
        if sid not in ordered:
            ordered.append(sid)
    return ordered


def _frontier_dock_candidates(
    sids: Sequence[int], capital_global: int,
) -> List[int]:
    ordered = sorted(int(s) for s in sids)
    if not ordered:
        return []
    total = len(ordered)
    preferred_local = max(1, total - 5)
    preferred = _local_to_global(sids, preferred_local)
    starter = set(_starter_cluster_global_sids(sids, capital_global))
    out: List[int] = []
    if preferred is not None and preferred not in starter:
        out.append(preferred)
    for sid in reversed(ordered):
        if sid in starter:
            continue
        if sid not in out:
            out.append(sid)
    # Last resort: allow starter-band sectors if the outer band is exhausted.
    if not out:
        out = list(reversed(ordered))
    return out


def _seed_commodities(station_class: StationClass, seed: str) -> Dict[str, Any]:
    # Lazy import — bang_import is heavy; only needed on reinject paths.
    from src.services.bang_import_service import _build_full_commodities

    return apply_class_pattern(
        _build_full_commodities({}),
        station_class,
        random.Random(seed),
    )


def _attach_market_prices(db: Session, station: Station) -> None:
    for row in build_market_prices(station.id, station.commodities or {}):
        db.add(row)


def place_capital_terra(
    db: Session, region: Region, capital_global: int,
) -> Optional[Planet]:
    """Phase 11: TERRA welcome planet at the Capital sector."""
    sector = _sector_row(db, region, capital_global)
    region_name = getattr(region, "name", None) or "Region"
    planet = Planet(
        name=f"{region_name} Welcome",
        sector_id=capital_global,
        sector_uuid=getattr(sector, "id", None) if sector is not None else None,
        region_id=region.id,
        type=PlanetType.TERRAN,
        status=PlanetStatus.DEVELOPED,
        habitability_score=95,
        max_population=8_000_000_000,
        max_colonists=1000,
        population=8_000_000_000,
        is_population_hub=True,
        description="Capital welcome planet (anchor-repair reinjection).",
    )
    db.add(planet)
    db.flush()
    return planet


def place_class1_commerce(
    db: Session,
    region: Region,
    sids: Sequence[int],
    capital_global: int,
    occupied: Set[int],
) -> Optional[Station]:
    """Phase 11: CLASS_1 at capital+1, then starter-cluster fallback."""
    capital_local = int(region.capital_sector_number or 1)
    sector_id = _pick_free(
        _class1_candidates(sids, capital_global, capital_local), occupied,
    )
    if sector_id is None:
        return None
    sector = _sector_row(db, region, sector_id)
    region_name = getattr(region, "name", None) or "Region"
    commodities = _seed_commodities(
        StationClass.CLASS_1, f"anchor-repair:{region.id}:class1:{sector_id}",
    )
    station = Station(
        name=f"{region_name} Commerce Hub",
        sector_id=sector_id,
        sector_uuid=getattr(sector, "id", None) if sector is not None else None,
        region_id=region.id,
        station_class=StationClass.CLASS_1,
        type=StationType.MINING,
        status=StationStatus.OPERATIONAL,
        commodities=commodities,
        defenses=Station.default_defenses_for_class(StationClass.CLASS_1),
        description="Class-1 commerce anchor (anchor-repair reinjection).",
    )
    db.add(station)
    db.flush()
    _attach_market_prices(db, station)
    occupied.add(sector_id)
    return station


def place_spacedock(
    db: Session,
    region: Region,
    role: str,
    candidates: Sequence[int],
    occupied: Set[int],
) -> Optional[Station]:
    """Phase 11 SpaceDock — CLASS_11 shipyard hub with assignment role."""
    sector_id = _pick_free(candidates, occupied)
    if sector_id is None:
        return None
    sector = _sector_row(db, region, sector_id)
    region_name = getattr(region, "name", None) or "Region"
    label = "Starter" if role == ROLE_STARTER else "Frontier"
    commodities = _seed_commodities(
        StationClass.CLASS_11,
        f"anchor-repair:{region.id}:spacedock:{role}:{sector_id}",
    )
    station = Station(
        name=f"{region_name} SpaceDock ({label})",
        sector_id=sector_id,
        sector_uuid=getattr(sector, "id", None) if sector is not None else None,
        region_id=region.id,
        station_class=StationClass.CLASS_11,
        type=StationType.SHIPYARD,
        status=StationStatus.OPERATIONAL,
        commodities=commodities,
        services=dict(_SPACEDOCK_SERVICES),
        is_spacedock=True,
        is_quest_hub=True,
        is_faction_headquarters=True,
        region_assignment_role=role,
        defenses=Station.default_defenses_for_class(StationClass.CLASS_11),
        description=f"SpaceDock {label} anchor (anchor-repair reinjection).",
    )
    db.add(station)
    db.flush()
    _attach_market_prices(db, station)
    occupied.add(sector_id)
    return station


def _repaired_event(
    region: Region, anchor_type: str, sector_id: Optional[int],
) -> Dict[str, Any]:
    return {
        "type": "region_anchor_repaired",
        "region_id": str(region.id),
        "region_name": getattr(region, "name", None),
        "anchor_type": anchor_type,
        "sector_id": sector_id,
    }


def _failed_event(
    region: Region, anchor_type: str, sector_id: Optional[int],
) -> Dict[str, Any]:
    return {
        "type": "region_anchor_repair_failed",
        "region_id": str(region.id),
        "region_name": getattr(region, "name", None),
        "anchor_type": anchor_type,
        "sector_id": sector_id,
    }


def reinject_missing_anchors(
    db: Session,
    region: Region,
    checks: Dict[str, str],
    *,
    sids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Create placement rows for each ``missing`` check. Idempotent under lock.

    Unverifiable SpaceDock roles are left alone (no false reinjection).
    """
    events: List[Dict[str, Any]] = []
    repaired = 0
    failed = 0
    missing = [k for k, v in checks.items() if v == "missing"]
    if not missing:
        return {"repaired": 0, "failed": 0, "events": events}

    _lock_region(db, region.id)
    sids = list(sids) if sids is not None else _region_sector_ids(db, region)
    capital = _region_capital_global_id(region, sids)
    capital_local = int(region.capital_sector_number or 1)
    occupied = _occupied_station_sids(db, region.id)

    # Re-check after lock so a concurrent repair does not double-place.
    fresh = scan_region(db, region)
    checks = fresh["checks"]

    if checks.get(ANCHOR_CAPITAL_TERRA) == "missing":
        if capital is None:
            events.append(_failed_event(region, ANCHOR_CAPITAL_TERRA, None))
            failed += 1
            logger.warning(
                "anchor_repair: repair_failed region=%s anchor=%s reason=no_capital",
                region.id, ANCHOR_CAPITAL_TERRA,
            )
        else:
            planet = place_capital_terra(db, region, capital)
            events.append(_repaired_event(region, ANCHOR_CAPITAL_TERRA, capital))
            repaired += 1
            logger.info(
                "anchor_repair: repaired region=%s anchor=%s sector=%s planet=%s",
                region.id, ANCHOR_CAPITAL_TERRA, capital, getattr(planet, "id", None),
            )

    if checks.get(ANCHOR_CLASS1_COMMERCE) == "missing":
        if capital is None:
            events.append(_failed_event(region, ANCHOR_CLASS1_COMMERCE, None))
            failed += 1
        else:
            station = place_class1_commerce(
                db, region, sids, capital, occupied,
            )
            if station is None:
                events.append(_failed_event(region, ANCHOR_CLASS1_COMMERCE, capital))
                failed += 1
                logger.warning(
                    "anchor_repair: repair_failed region=%s anchor=%s reason=no_sector",
                    region.id, ANCHOR_CLASS1_COMMERCE,
                )
            else:
                events.append(
                    _repaired_event(region, ANCHOR_CLASS1_COMMERCE, station.sector_id),
                )
                repaired += 1
                logger.info(
                    "anchor_repair: repaired region=%s anchor=%s sector=%s station=%s",
                    region.id, ANCHOR_CLASS1_COMMERCE, station.sector_id, station.id,
                )

    if checks.get(ANCHOR_SPACEDOCK_STARTER) == "missing" and capital is not None:
        station = place_spacedock(
            db,
            region,
            ROLE_STARTER,
            _starter_dock_candidates(sids, capital, capital_local),
            occupied,
        )
        if station is None:
            events.append(_failed_event(region, ANCHOR_SPACEDOCK_STARTER, capital))
            failed += 1
            logger.warning(
                "anchor_repair: repair_failed region=%s anchor=%s reason=no_sector",
                region.id, ANCHOR_SPACEDOCK_STARTER,
            )
        else:
            events.append(
                _repaired_event(region, ANCHOR_SPACEDOCK_STARTER, station.sector_id),
            )
            repaired += 1
            logger.info(
                "anchor_repair: repaired region=%s anchor=%s sector=%s station=%s",
                region.id, ANCHOR_SPACEDOCK_STARTER, station.sector_id, station.id,
            )

    if checks.get(ANCHOR_SPACEDOCK_FRONTIER) == "missing" and capital is not None:
        station = place_spacedock(
            db,
            region,
            ROLE_FRONTIER,
            _frontier_dock_candidates(sids, capital),
            occupied,
        )
        if station is None:
            events.append(_failed_event(region, ANCHOR_SPACEDOCK_FRONTIER, capital))
            failed += 1
            logger.warning(
                "anchor_repair: repair_failed region=%s anchor=%s reason=no_sector",
                region.id, ANCHOR_SPACEDOCK_FRONTIER,
            )
        else:
            events.append(
                _repaired_event(region, ANCHOR_SPACEDOCK_FRONTIER, station.sector_id),
            )
            repaired += 1
            logger.info(
                "anchor_repair: repaired region=%s anchor=%s sector=%s station=%s",
                region.id, ANCHOR_SPACEDOCK_FRONTIER, station.sector_id, station.id,
            )

    return {"repaired": repaired, "failed": failed, "events": events}


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
    """Run the four existence checks for one region."""
    sids = _region_sector_ids(db, region)
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
    return {"checks": results, "events": events, "sids": sids, "capital": capital}


def run_daily_scan(db: Session) -> Dict[str, Any]:
    """Scan every active non-nexus region and reinject missing anchors.

    Flush-only; caller owns commit. Returns
    ``{regions_scanned, missing_count, unverifiable_count, repaired_count,
    failed_count, events}``.
    """
    regions = db.query(Region).all()
    events: List[Dict[str, Any]] = []
    scanned = 0
    missing = 0
    unverifiable = 0
    repaired = 0
    failed = 0
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
        if any(v == "missing" for v in outcome["checks"].values()):
            repair = reinject_missing_anchors(
                db, region, outcome["checks"], sids=outcome.get("sids"),
            )
            events.extend(repair["events"])
            repaired += int(repair["repaired"])
            failed += int(repair["failed"])
    return {
        "regions_scanned": scanned,
        "missing_count": missing,
        "unverifiable_count": unverifiable,
        "repaired_count": repaired,
        "failed_count": failed,
        "events": events,
    }


def run_daily_scan_gated(db: Session) -> Dict[str, Any]:
    """Once-per-canonical-day gate around ``run_daily_scan``.

    Mirrors ``_run_region_lifecycle_advance_gated``'s Galaxy.state day-anchor
    discipline. Intended to ride governance Phase 8 under the shared
    governance advisory lock (caller owns commit).

    Returns {anchor_repair_skipped, anchors_scanned, anchors_missing,
    unverifiable_count, repaired_count, failed_count, events}.
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
        "repaired_count": 0,
        "failed_count": 0,
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
    result["repaired_count"] = scan.get("repaired_count", 0)
    result["failed_count"] = scan.get("failed_count", 0)
    result["events"] = list(scan.get("events") or [])

    if galaxy is not None:
        gstate = dict(galaxy.state or {})
        gstate[_ANCHOR_REPAIR_STATE_KEY] = this_day
        galaxy.state = gstate
        flag_modified(galaxy, "state")
    return result
