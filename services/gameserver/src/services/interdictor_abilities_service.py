"""Police Interdictor hull special abilities (police-forces.md:154-196).

Marshal Interdictor: Interdictor Field, Contraband Scanner.
Sentinel Interdictor: Interdictor Field, Beacon Disruptor, Concord Authorization.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipType
from src.models.warp_gate import WarpGateBeacon, WarpGateBeaconStatus

logger = logging.getLogger(__name__)

INTERDICTOR_FIELD_KEY = "_interdictor_field"
INTERDICTOR_FIELD_ROUNDS = 3
INFINITY_TURN_COST = 999_999

ABILITIES_BY_SHIP_TYPE: Dict[ShipType, List[str]] = {
    ShipType.NPC_MARSHAL_INTERDICTOR: ["interdictor_field", "contraband_scanner"],
    ShipType.NPC_SENTINEL_INTERDICTOR: [
        "interdictor_field",
        "beacon_disruptor",
        "concord_authorization",
    ],
}


def ship_has_ability(ship: Optional[Ship], ability: str) -> bool:
    """True when the hull carries the named special ability."""
    if ship is None or ability is None:
        return False
    ship_type = getattr(ship, "type", None)
    if ship_type in ABILITIES_BY_SHIP_TYPE:
        if ability in ABILITIES_BY_SHIP_TYPE[ship_type]:
            return True
    slots = getattr(ship, "special_abilities", None)
    if isinstance(slots, list) and ability in slots:
        return True
    return False


def get_interdictor_field_state(ship: Optional[Ship]) -> Dict[str, Any]:
    if ship is None:
        return {}
    slots = getattr(ship, "equipment_slots", None) or {}
    if not isinstance(slots, dict):
        return {}
    state = slots.get(INTERDICTOR_FIELD_KEY)
    return state if isinstance(state, dict) else {}


def interdictor_field_rounds_remaining(ship: Optional[Ship]) -> int:
    state = get_interdictor_field_state(ship)
    try:
        return max(0, int(state.get("rounds_remaining", 0) or 0))
    except (TypeError, ValueError):
        return 0


def is_interdictor_field_active(ship: Optional[Ship]) -> bool:
    return interdictor_field_rounds_remaining(ship) > 0


def interdictor_field_blocks_movement(ship: Optional[Ship]) -> bool:
    """Canon: while the field holds, warp/jump/gate/slipdrive are disabled."""
    return is_interdictor_field_active(ship)


def movement_block_message() -> str:
    return (
        "Interdictor Field active — warp, slipdrive, quantum jump, and "
        "player warp gates are disabled for this ship"
    )


def apply_interdictor_field(
    interdictor_ship: Ship,
    target_ship: Ship,
    *,
    rounds: int = INTERDICTOR_FIELD_ROUNDS,
) -> bool:
    """Engage the field on a single target (no stacking per target)."""
    if not ship_has_ability(interdictor_ship, "interdictor_field"):
        return False
    if target_ship is None:
        return False
    existing = get_interdictor_field_state(target_ship)
    if existing.get("source_ship_id") == str(interdictor_ship.id):
        # Refresh duration — still single-target, no stacking bonus.
        existing["rounds_remaining"] = rounds
    else:
        existing = {
            "rounds_remaining": rounds,
            "source_ship_id": str(interdictor_ship.id),
        }
    slots = dict(getattr(target_ship, "equipment_slots", None) or {})
    slots[INTERDICTOR_FIELD_KEY] = existing
    target_ship.equipment_slots = slots
    flag_modified(target_ship, "equipment_slots")
    return True


def decrement_interdictor_field_round(target_ship: Optional[Ship]) -> int:
    """Consume one combat round of field duration; returns rounds left."""
    if target_ship is None:
        return 0
    remaining = interdictor_field_rounds_remaining(target_ship)
    if remaining <= 0:
        return 0
    remaining -= 1
    slots = dict(getattr(target_ship, "equipment_slots", None) or {})
    if remaining <= 0:
        slots.pop(INTERDICTOR_FIELD_KEY, None)
    else:
        state = dict(get_interdictor_field_state(target_ship))
        state["rounds_remaining"] = remaining
        slots[INTERDICTOR_FIELD_KEY] = state
    target_ship.equipment_slots = slots
    flag_modified(target_ship, "equipment_slots")
    return remaining


def clear_interdictor_field(target_ship: Optional[Ship]) -> None:
    if target_ship is None:
        return
    slots = dict(getattr(target_ship, "equipment_slots", None) or {})
    if INTERDICTOR_FIELD_KEY in slots:
        slots.pop(INTERDICTOR_FIELD_KEY, None)
        target_ship.equipment_slots = slots
        flag_modified(target_ship, "equipment_slots")


def warp_turn_cost_with_interdictor_field(
    base_cost: int,
    ship: Optional[Ship],
) -> int:
    """Clamp turn_cost reads to infinity while the field is active."""
    if is_interdictor_field_active(ship):
        return INFINITY_TURN_COST
    return base_cost


def sector_has_contraband_scanner_patrol(db: Session, sector: Sector) -> bool:
    """True when a Federation Marshal patrol (Contraband Scanner hull) is present."""
    patrols = (sector.defenses or {}).get("police_patrol_ships")
    if not isinstance(patrols, list):
        return False
    for patrol in patrols:
        if not isinstance(patrol, dict):
            continue
        squad_kind = patrol.get("squad_kind")
        if squad_kind == "federation_marshal":
            return True
        npc_ids = patrol.get("npc_character_ids") or []
        for raw_id in npc_ids:
            try:
                npc_uuid = uuid.UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            from src.models.npc_character import NPCCharacter

            npc = db.query(NPCCharacter).filter(NPCCharacter.id == npc_uuid).first()
            if npc is None or npc.ship_id is None:
                continue
            npc_ship = db.query(Ship).filter(Ship.id == npc.ship_id).first()
            if ship_has_ability(npc_ship, "contraband_scanner"):
                return True
    return False


def _adjacent_sector_numbers(db: Session, center_sector_number: int) -> Set[int]:
    center = db.query(Sector).filter(Sector.sector_id == center_sector_number).first()
    if center is None:
        return {center_sector_number}
    neighbors: Set[int] = {center_sector_number}
    rows = db.execute(
        sector_warps.select().where(
            sector_warps.c.source_sector_id == center.id,
        )
    ).fetchall()
    for row in rows:
        dest = db.query(Sector).filter(Sector.id == row.destination_sector_id).first()
        if dest is not None:
            neighbors.add(dest.sector_id)
    reverse_rows = db.execute(
        sector_warps.select().where(
            sector_warps.c.destination_sector_id == center.id,
            sector_warps.c.is_bidirectional == True,  # noqa: E712
        )
    ).fetchall()
    for row in reverse_rows:
        origin = db.query(Sector).filter(Sector.id == row.source_sector_id).first()
        if origin is not None:
            neighbors.add(origin.sector_id)
    return neighbors


def disrupt_phase1_beacons_near_sector(
    db: Session,
    center_sector_number: int,
    *,
    disruptor_ship: Optional[Ship] = None,
) -> List[Dict[str, Any]]:
    """Beacon Disruptor — cancel DEPLOYED Phase-1 beacons in-sector + adjacent.

    Materials are sunk (no refund) per police-forces.md.
    """
    if disruptor_ship is not None and not ship_has_ability(disruptor_ship, "beacon_disruptor"):
        return []
    sector_numbers = _adjacent_sector_numbers(db, center_sector_number)
    cancelled: List[Dict[str, Any]] = []
    beacons = (
        db.query(WarpGateBeacon)
        .filter(
            WarpGateBeacon.status == WarpGateBeaconStatus.DEPLOYED,
            WarpGateBeacon.source_sector_id.in_(sector_numbers),
        )
        .all()
    )
    for beacon in beacons:
        beacon.status = WarpGateBeaconStatus.CANCELLED
        cancelled.append(
            {
                "beacon_id": str(beacon.id),
                "source_sector_id": beacon.source_sector_id,
                "destination_sector_id": beacon.destination_sector_id,
            }
        )
    if cancelled:
        db.flush()
        logger.info(
            "Beacon Disruptor cancelled %s Phase-1 beacon(s) near sector %s",
            len(cancelled),
            center_sector_number,
        )
    return cancelled


def maybe_apply_interdictor_on_npc_attack(
    attacker_ship: Optional[Ship],
    defender_ship: Optional[Ship],
) -> bool:
    """Apply Interdictor Field when an Interdictor NPC engages a player hull."""
    if attacker_ship is None or defender_ship is None:
        return False
    if getattr(defender_ship, "is_npc", False):
        return False
    return apply_interdictor_field(attacker_ship, defender_ship)
