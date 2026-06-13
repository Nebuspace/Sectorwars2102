"""NPC mission service — colonist couriers and science vessels.

Beyond commodity merchants, some NPC captains run *purposeful* circuits that
make the colonist economy feel alive:

  - COLONIST couriers load colonists at a population hub (New Earth) and ferry
    them to under-populated planets — owned OR unclaimed — actually GROWING the
    target planet's population. They carry the colonists in their ship hold, so
    a player who kills one loots the cargo (the combat loot path already moves
    all cargo to the victor).

  - SCIENCE vessels visit uninhabited planets on survey runs (flavor + variety;
    no economic effect today).

Both reuse the trader movement spine: travel is the existing COMMUTE block
(``_drive_commute`` toward a sector), and the action happens in a ``mission_stop``
work block that the scheduler routes to ``run_mission_stop`` here.

Route shape (stored in ``daily_schedule.mission_route``):
    [{"sector_id": int, "planet_id": str, "action": "load"|"deliver"|"survey"}]
"""

import logging
import random
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.planet import Planet
from src.models.sector import Sector
from src.models.ship import Ship

logger = logging.getLogger(__name__)

COLONIST_MISSION = "colonist"
SCIENCE_MISSION = "science"

# How far (warp hops) a mission route may reach from its anchor when picking
# stops, and how many delivery/survey stops a route gets.
MISSION_HOP_BUDGET = 14
COLONIST_STOPS = 3
SCIENCE_STOPS = 4
# Colonists moved per delivery (clamped to the planet's remaining capacity and
# the cargo actually aboard).
COLONIST_DROP = 250


def _reachable_sectors(db: Session, origin_sector_id: int) -> Dict[int, int]:
    """Sectors reachable from origin within MISSION_HOP_BUDGET (BFS over the
    warp graph, shared with route/engagement code). {sector_id: hops}."""
    from src.services.npc_engagement_service import _hop_distances
    return _hop_distances(db, origin_sector_id, MISSION_HOP_BUDGET)


def _population_hub(db: Session) -> Optional[Planet]:
    return (
        db.query(Planet)
        .filter(Planet.is_population_hub.is_(True), Planet.sector_id.isnot(None))
        .first()
    )


def generate_colonist_route(db: Session, home_sector_id: int) -> Optional[List[Dict[str, Any]]]:
    """Build a courier circuit: load at the population hub, then deliver to
    under-populated planets reachable from the hub. Returns None when no hub or
    no reachable targets exist."""
    hub = _population_hub(db)
    if hub is None:
        return None
    reach = _reachable_sectors(db, hub.sector_id)
    if not reach:
        return None
    candidates = (
        db.query(Planet)
        .filter(
            Planet.is_population_hub.isnot(True),
            Planet.population < Planet.max_population,
            Planet.sector_id.isnot(None),
        )
        .all()
    )
    targets = [p for p in candidates if p.sector_id in reach and p.sector_id != hub.sector_id]
    if not targets:
        return None
    random.shuffle(targets)
    route: List[Dict[str, Any]] = [
        {"sector_id": hub.sector_id, "planet_id": str(hub.id), "action": "load"}
    ]
    for p in targets[:COLONIST_STOPS]:
        route.append({"sector_id": p.sector_id, "planet_id": str(p.id), "action": "deliver"})
    return route if len(route) >= 2 else None


def generate_science_route(db: Session, home_sector_id: int) -> Optional[List[Dict[str, Any]]]:
    """Build a survey circuit of uninhabited planets reachable from home."""
    reach = _reachable_sectors(db, home_sector_id)
    if not reach:
        return None
    candidates = (
        db.query(Planet)
        .filter(Planet.population == 0, Planet.sector_id.isnot(None))
        .all()
    )
    targets = [p for p in candidates if p.sector_id in reach]
    if not targets:
        return None
    random.shuffle(targets)
    route = [
        {"sector_id": p.sector_id, "planet_id": str(p.id), "action": "survey"}
        for p in targets[:SCIENCE_STOPS]
    ]
    return route if route else None


def build_mission_schedule(route: List[Dict[str, Any]], mission: str) -> Dict[str, Any]:
    """Multi-day schedule for a mission circuit: per stop a transit day
    (sleep + commute to the stop's sector) and an action day (mission_stop).
    Shape mirrors build_trader_schedule so the scheduler resolves it the same
    way; the mission + route are stored for run_mission_stop."""
    days: Dict[str, List[Dict[str, Any]]] = {}
    for i, stop in enumerate(route):
        days[str(2 * i)] = [
            {"start_minute": 0, "end_minute": 360, "activity": "sleep",
             "location_type": "ship", "location_ref": None},
            {"start_minute": 360, "end_minute": 1440, "activity": "commute",
             "location_type": "station_target",
             "location_ref": {"sector_id": stop["sector_id"]}},
        ]
        days[str(2 * i + 1)] = [
            {"start_minute": 0, "end_minute": 360, "activity": "sleep",
             "location_type": "ship", "location_ref": None},
            {"start_minute": 360, "end_minute": 1440, "activity": "work_station",
             "location_type": "mission_stop",
             "location_ref": {
                 "sector_id": stop["sector_id"],
                 "planet_id": stop["planet_id"],
                 "action": stop["action"],
                 "stop_index": i,
             }},
        ]
    return {
        "route_cycle": {"cycle_days": 2 * len(route), "days": days},
        "mission": mission,
        "mission_route": route,
    }


def _cargo_colonists(ship: Ship) -> int:
    return int(((ship.cargo or {}).get("contents") or {}).get("colonists", 0) or 0)


def _set_cargo_colonists(ship: Ship, value: int) -> None:
    cargo = dict(ship.cargo or {})
    contents = dict(cargo.get("contents") or {})
    if value > 0:
        contents["colonists"] = value
    else:
        contents.pop("colonists", None)
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")


def run_mission_stop(db: Session, npc, stop: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute a mission stop's action at the NPC's current sector.

    load    — fill the hold with colonists from the hub (effectively unlimited).
    deliver — move colonists from the hold into the target planet's population
              (capped by remaining capacity), real growth that players can also
              benefit from or intercept.
    survey  — flavor (no economic effect yet).
    Flush-only; the caller owns the commit. Returns broadcastable events."""
    action = stop.get("action")
    ship = db.query(Ship).filter(Ship.id == npc.ship_id).first() if npc.ship_id else None
    if ship is None:
        return []

    if action == "load":
        capacity = int((ship.cargo or {}).get("capacity", 0) or 0)
        if capacity <= 0:
            return []
        used_other = sum(
            int(q) for k, q in ((ship.cargo or {}).get("contents") or {}).items()
            if k != "colonists" and isinstance(q, (int, float))
        )
        room = max(0, capacity - used_other)
        if room <= 0:
            return []
        _set_cargo_colonists(ship, room)
        db.flush()
        logger.info("Mission: %s loaded %d colonists at hub (sector %s)",
                    npc.display_name, room, npc.current_sector_id)
        return [{
            "type": "npc_colonists_loaded", "sector_id": npc.current_sector_id,
            "npc_id": str(npc.id), "amount": room,
        }]

    if action == "deliver":
        carried = _cargo_colonists(ship)
        if carried <= 0:
            return []
        planet = None
        pid = stop.get("planet_id")
        if pid:
            planet = db.query(Planet).filter(Planet.id == pid).with_for_update().first()
        if planet is None or planet.sector_id != npc.current_sector_id:
            return []
        room = max(0, int(planet.max_population or 0) - int(planet.population or 0))
        delivered = min(carried, room, COLONIST_DROP)
        if delivered <= 0:
            return []
        planet.population = int(planet.population or 0) + delivered
        _set_cargo_colonists(ship, carried - delivered)
        db.flush()
        logger.info("Mission: %s delivered %d colonists to %s (pop now %d)",
                    npc.display_name, delivered, planet.name, planet.population)
        return [{
            "type": "npc_colonists_delivered", "sector_id": npc.current_sector_id,
            "npc_id": str(npc.id), "planet_id": str(planet.id), "amount": delivered,
        }]

    if action == "survey":
        logger.info("Mission: %s surveying uninhabited world in sector %s",
                    npc.display_name, npc.current_sector_id)
        return [{
            "type": "npc_survey", "sector_id": npc.current_sector_id,
            "npc_id": str(npc.id),
        }]

    return []
