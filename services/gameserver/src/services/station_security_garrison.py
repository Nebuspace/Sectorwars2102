"""Station-funded STATION_SECURITY garrison wiring (LEG-299).

Canon: FEATURES/economy/station-protection.md § Roster + Station.security
JSONB shape. Magnitudes are the documented roster table only:

- Basic: 0 named guards (drones stay anonymous / already elsewhere)
- Standard: 2 named guards; barracks capacity 2; no captain
- Premium: 4 named guards + 1 named Captain; barracks capacity 5

``guard_npc_ids`` holds the routine-guard ids only. ``guard_captain_npc_id``
holds the Premium captain (not duplicated in ``guard_npc_ids``).

Does not invent wages, shift overlap, KIA respawn, or Interdictor hulls.
Routine guards fly Light Freighter; the Premium captain flies Defender.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, NamedTuple, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.npc_barracks import NPCBarracks, NPCLodgingLocationType
from src.models.npc_character import NPCActivity, NPCArchetype, NPCCharacter, NPCLifecycleStage, NPCStatus
from src.models.ship import ShipSpecification, ShipType
from src.models.station import Station
from src.services.npc_spawn_service import _build_npc_ship

logger = logging.getLogger(__name__)

# Fictional roster labels — not real-person names.
_GUARD_NAMES = (
    "Ashen Pike",
    "Copper Wren",
    "Iron Hearth",
    "Silent Quay",
)
_CAPTAIN_NAME = "Nimbus Vale"


class GarrisonSpec(NamedTuple):
    routine_guards: int
    captains: int
    barracks_capacity: int


def garrison_spec_for_tier(tier: Optional[str]) -> GarrisonSpec:
    """Canon roster table (station-protection.md:53-57). Unknown/none → empty."""
    key = (tier or "none").lower()
    if key == "standard":
        return GarrisonSpec(routine_guards=2, captains=0, barracks_capacity=2)
    if key == "premium":
        return GarrisonSpec(routine_guards=4, captains=1, barracks_capacity=5)
    return GarrisonSpec(routine_guards=0, captains=0, barracks_capacity=0)


def _faction_code(station: Station) -> str:
    return (station.faction_affiliation or "independent").strip() or "independent"


def _security_dict(station: Station) -> Dict[str, Any]:
    if not isinstance(station.security, dict):
        station.security = {"tier": "none"}
    return station.security


def _spec_for_type(db: Session, ship_type: ShipType) -> Optional[ShipSpecification]:
    return db.query(ShipSpecification).filter(ShipSpecification.type == ship_type).first()


def _existing_station_barracks(db: Session, station_id) -> Optional[NPCBarracks]:
    return (
        db.query(NPCBarracks)
        .filter(
            NPCBarracks.station_id == station_id,
            NPCBarracks.archetype == NPCArchetype.STATION_SECURITY,
        )
        .first()
    )


def _npcs_at_barracks(db: Session, barracks_id) -> List[NPCCharacter]:
    q = db.query(NPCCharacter).filter(NPCCharacter.home_barracks_id == barracks_id)
    if hasattr(q, "all"):
        return list(q.all())
    first = q.first()
    return [first] if first is not None else []


def _ensure_barracks(db: Session, station: Station, capacity: int) -> NPCBarracks:
    row = _existing_station_barracks(db, station.id)
    if row is not None:
        if row.capacity != capacity:
            row.capacity = capacity
        return row
    if station.region_id is None:
        raise ValueError("station.region_id is required to persist NPCBarracks")
    row = NPCBarracks(
        name=f"{station.name} Security Barracks",
        location_type=NPCLodgingLocationType.STATION,
        station_id=station.id,
        sector_id=None,
        home_region_id=station.region_id,
        faction_code=_faction_code(station),
        archetype=NPCArchetype.STATION_SECURITY,
        capacity=capacity,
        amenities={"quarters_type": "shared", "station_funded": True},
    )
    db.add(row)
    db.flush()
    if getattr(row, "id", None) is None:
        row.id = uuid.uuid4()
    return row


def _spawn_guard(
    db: Session,
    *,
    station: Station,
    barracks: NPCBarracks,
    name: str,
    title: str,
    duty_role: str,
    ship_type: ShipType,
    now: datetime,
) -> NPCCharacter:
    spec = _spec_for_type(db, ship_type)
    if spec is None:
        raise ValueError(f"missing ShipSpecification for {ship_type}")
    ship = _build_npc_ship(
        spec,
        name=f"{title} {name}'s {ship_type.name.replace('_', ' ').title()}",
        sector_id=int(station.sector_id),
    )
    db.add(ship)
    db.flush()
    if getattr(ship, "id", None) is None:
        ship.id = uuid.uuid4()
    npc = NPCCharacter(
        name=name,
        title=title,
        faction_code=_faction_code(station),
        archetype=NPCArchetype.STATION_SECURITY,
        status=NPCStatus.ON_DUTY,
        current_activity=NPCActivity.PATROL,
        current_sector_id=int(station.sector_id),
        ship_id=ship.id,
        home_region_id=station.region_id,
        home_barracks_id=barracks.id,
        duty_role=duty_role,
        lifecycle_stage=NPCLifecycleStage.ACTIVE,
        spawned_at=now,
        last_seen_at=now,
    )
    db.add(npc)
    db.flush()
    if getattr(npc, "id", None) is None:
        npc.id = uuid.uuid4()
    assigned = list(barracks.assigned_npc_ids or [])
    sid = str(npc.id)
    if sid not in assigned:
        assigned.append(sid)
        barracks.assigned_npc_ids = assigned
        barracks.current_occupants_count = len(assigned)
    return npc


def ensure_station_security_garrison(db: Session, station: Station) -> Dict[str, Any]:
    """Create/refresh the named-guard roster for ``station.security_level``.

    Idempotent: a fully staffed Standard/Premium garrison is left in place.
    Basic/none clears the JSONB roster keys and does not spawn NPCs.
    Caller owns the transaction (flush only; no commit).
    """
    now = datetime.now(UTC)
    spec = garrison_spec_for_tier(station.security_level)
    sec = _security_dict(station)

    if spec.routine_guards == 0 and spec.captains == 0:
        sec["guard_npc_ids"] = []
        sec["guard_captain_npc_id"] = None
        # Basic has no named barracks occupancy requirement.
        flag_modified(station, "security")
        db.flush()
        return {
            "tier": station.security_level,
            "barracks_id": sec.get("barracks_id"),
            "guard_npc_ids": [],
            "guard_captain_npc_id": None,
            "spawned": False,
        }

    barracks = _ensure_barracks(db, station, spec.barracks_capacity)
    existing = _npcs_at_barracks(db, barracks.id)
    security_npcs = [n for n in existing if n.archetype == NPCArchetype.STATION_SECURITY]
    captains = [n for n in security_npcs if (n.duty_role or "") == "station_security_captain"]
    guards = [n for n in security_npcs if n not in captains]

    spawned_any = False
    while len(guards) < spec.routine_guards:
        idx = len(guards)
        npc = _spawn_guard(
            db,
            station=station,
            barracks=barracks,
            name=_GUARD_NAMES[idx % len(_GUARD_NAMES)],
            title="Guard",
            duty_role="station_security_guard",
            ship_type=ShipType.LIGHT_FREIGHTER,
            now=now,
        )
        guards.append(npc)
        spawned_any = True
    while len(captains) < spec.captains:
        npc = _spawn_guard(
            db,
            station=station,
            barracks=barracks,
            name=_CAPTAIN_NAME,
            title="Guard-Captain",
            duty_role="station_security_captain",
            ship_type=ShipType.DEFENDER,
            now=now,
        )
        captains.append(npc)
        spawned_any = True

    routine = guards[: spec.routine_guards]
    captain = captains[0] if spec.captains else None
    sec["barracks_id"] = str(barracks.id)
    sec["guard_npc_ids"] = [str(n.id) for n in routine]
    sec["guard_captain_npc_id"] = str(captain.id) if captain is not None else None
    flag_modified(station, "security")
    db.flush()
    return {
        "tier": station.security_level,
        "barracks_id": sec["barracks_id"],
        "guard_npc_ids": list(sec["guard_npc_ids"]),
        "guard_captain_npc_id": sec["guard_captain_npc_id"],
        "spawned": spawned_any,
    }


def ensure_garrison_from_barracks(db: Session, barracks: NPCBarracks) -> Optional[Dict[str, Any]]:
    """WO accept path: an existing station NPCBarracks row gets a garrison.

    Looks up the host Station and uses that station's security_level roster.
    """
    if barracks.station_id is None:
        return None
    station = db.query(Station).filter(Station.id == barracks.station_id).first()
    if station is None:
        logger.warning("LEG-299: barracks %s station %s missing", barracks.id, barracks.station_id)
        return None
    return ensure_station_security_garrison(db, station)
