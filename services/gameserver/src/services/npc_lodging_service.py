"""npc_lodging_service — sleep/wake occupancy + docked_npc_ships shielding.

Canon: DATA_MODELS/npc-lodging.md, SYSTEMS/npc-scheduler.md Loop C,
SYSTEMS/npc-lifecycle.md off-duty ship parking.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.npc_barracks import NPCBarracks, NPCLodgingLocationType
from src.models.npc_character import NPCActivity, NPCCharacter
from src.models.outlaw_base import OutlawBase
from src.models.sector import Sector

logger = logging.getLogger(__name__)

LODGING_TYPE_BARRACKS = "barracks"
LODGING_TYPE_OUTLAW_BASE = "outlaw_base"
DOCKED_OFF_DUTY = "docked_off_duty"
ERR_NPC_SHIP_AT_BARRACKS = "ERR_NPC_SHIP_AT_BARRACKS"


def sync_sector_lodging_flags(
    db: Session, sector_ids: Optional[List[int]] = None
) -> Dict[str, int]:
    """Recompute ``Sector.is_outlaw_zone`` / ``is_npc_barracks_sector``.

    Canon (DATA_MODELS/npc-lodging.md): outlaw flag tracks OutlawBase rows;
    barracks flag tracks NPCBarracks with ``location_type=sector``. Idempotent
    backfill — call after lodging place/move/delete, or with ``sector_ids=None``
    to refresh every sector that hosts lodging or already carries a flag.
    """
    outlaw_q = db.query(OutlawBase.sector_id)
    barracks_q = (
        db.query(NPCBarracks.sector_id)
        .filter(NPCBarracks.location_type == NPCLodgingLocationType.SECTOR)
        .filter(NPCBarracks.sector_id.isnot(None))
    )
    if sector_ids is not None:
        sid_filter = list({int(s) for s in sector_ids})
        if not sid_filter:
            return {"sectors_checked": 0, "sectors_updated": 0}
        outlaw_q = outlaw_q.filter(OutlawBase.sector_id.in_(sid_filter))
        barracks_q = barracks_q.filter(NPCBarracks.sector_id.in_(sid_filter))
        targets = set(sid_filter)
    else:
        targets = set()

    outlaw_sectors = {int(sid) for (sid,) in outlaw_q.all() if sid is not None}
    barracks_sectors = {int(sid) for (sid,) in barracks_q.all() if sid is not None}

    if sector_ids is None:
        flagged = (
            db.query(Sector.sector_id)
            .filter(
                (Sector.is_outlaw_zone.is_(True))
                | (Sector.is_npc_barracks_sector.is_(True))
            )
            .all()
        )
        targets = outlaw_sectors | barracks_sectors | {int(sid) for (sid,) in flagged}

    updated = 0
    for sid in targets:
        sector = db.query(Sector).filter(Sector.sector_id == sid).first()
        if sector is None:
            continue
        want_outlaw = sid in outlaw_sectors
        want_barracks = sid in barracks_sectors
        if (
            bool(getattr(sector, "is_outlaw_zone", False)) != want_outlaw
            or bool(getattr(sector, "is_npc_barracks_sector", False)) != want_barracks
        ):
            sector.is_outlaw_zone = want_outlaw
            sector.is_npc_barracks_sector = want_barracks
            updated += 1

    return {"sectors_checked": len(targets), "sectors_updated": updated}


def refresh_sector_lodging_flags(db: Session, *sector_ids: int) -> Dict[str, int]:
    """Incremental hook for lodging place/move/delete paths."""
    return sync_sector_lodging_flags(db, sector_ids=list(sector_ids))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _npc_id_str(npc_id: UUID) -> str:
    return str(npc_id)


def resolve_home_lodging(
    db: Session, npc: NPCCharacter
) -> tuple[Optional[Any], Optional[str]]:
    """Return (lodging_row, lodging_type) for the NPC's home lodging."""
    if npc.home_barracks_id is not None:
        row = db.query(NPCBarracks).filter(NPCBarracks.id == npc.home_barracks_id).first()
        return row, LODGING_TYPE_BARRACKS
    if npc.home_outlaw_base_id is not None:
        row = db.query(OutlawBase).filter(OutlawBase.id == npc.home_outlaw_base_id).first()
        return row, LODGING_TYPE_OUTLAW_BASE
    return None, None


def apply_roster_lodging_to_npc(npc: NPCCharacter, roster) -> None:
    """Copy NPCRoster.default_lodging_* onto the character's home FKs."""
    lodging_id = getattr(roster, "default_lodging_id", None)
    lodging_type = (getattr(roster, "default_lodging_type", None) or "").lower()
    if not lodging_id or not lodging_type:
        return
    if lodging_type in (LODGING_TYPE_BARRACKS, "npc_barracks"):
        npc.home_barracks_id = lodging_id
        npc.home_outlaw_base_id = None
    elif lodging_type in (LODGING_TYPE_OUTLAW_BASE, "outlawbase", "outlaw_base"):
        npc.home_outlaw_base_id = lodging_id
        npc.home_barracks_id = None


def _append_occupant(lodging, npc_id: UUID) -> None:
    ids: List[str] = list(lodging.assigned_npc_ids or [])
    sid = _npc_id_str(npc_id)
    if sid not in ids:
        ids.append(sid)
        lodging.assigned_npc_ids = ids
        lodging.current_occupants_count = len(ids)
        if lodging.current_occupants_count > lodging.capacity:
            logger.warning(
                "lodging %s occupancy %s exceeds capacity %s (advisory)",
                lodging.id,
                lodging.current_occupants_count,
                lodging.capacity,
            )


def _remove_occupant(lodging, npc_id: UUID) -> None:
    ids: List[str] = [i for i in (lodging.assigned_npc_ids or []) if i != _npc_id_str(npc_id)]
    lodging.assigned_npc_ids = ids
    lodging.current_occupants_count = len(ids)


def _dock_ship(db: Session, npc: NPCCharacter, status: str = DOCKED_OFF_DUTY) -> None:
    if npc.ship_id is None or npc.current_sector_id is None:
        return
    sector = (
        db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
    )
    if sector is None:
        return
    defenses = dict(sector.defenses or {})
    docked: List[Dict[str, Any]] = list(defenses.get("docked_npc_ships") or [])
    sid = _npc_id_str(npc.id)
    docked = [e for e in docked if e.get("npc_id") != sid]
    docked.append(
        {
            "npc_id": sid,
            "ship_id": str(npc.ship_id),
            "status": status,
            "docked_at": _now().isoformat(),
        }
    )
    defenses["docked_npc_ships"] = docked
    sector.defenses = defenses
    if hasattr(sector, "_sa_instance_state"):
        flag_modified(sector, "defenses")


def _undock_ship(db: Session, npc: NPCCharacter) -> None:
    if npc.current_sector_id is None:
        return
    sector = (
        db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
    )
    if sector is None:
        return
    defenses = dict(sector.defenses or {})
    docked = [
        e
        for e in (defenses.get("docked_npc_ships") or [])
        if e.get("npc_id") != _npc_id_str(npc.id)
    ]
    if len(docked) == len(defenses.get("docked_npc_ships") or []):
        return
    defenses["docked_npc_ships"] = docked
    sector.defenses = defenses
    if hasattr(sector, "_sa_instance_state"):
        flag_modified(sector, "defenses")


def enter_sleep(db: Session, npc: NPCCharacter) -> bool:
    """Mark NPC as occupying home lodging + dock ship off-duty. Returns True if applied."""
    lodging, _kind = resolve_home_lodging(db, npc)
    if lodging is None:
        return False
    _append_occupant(lodging, npc.id)
    _dock_ship(db, npc, DOCKED_OFF_DUTY)
    return True


def leave_sleep(db: Session, npc: NPCCharacter) -> bool:
    """Remove NPC from lodging occupancy + undock ship. Returns True if applied."""
    lodging, _kind = resolve_home_lodging(db, npc)
    if lodging is None:
        _undock_ship(db, npc)
        return False
    _remove_occupant(lodging, npc.id)
    _undock_ship(db, npc)
    return True


def is_npc_ship_barracks_shielded(db: Session, ship_id: UUID) -> bool:
    """True when the ship's piloting NPC is listed docked_off_duty in sector defenses."""
    npc = db.query(NPCCharacter).filter(NPCCharacter.ship_id == ship_id).first()
    if npc is None or npc.current_sector_id is None:
        return False
    sector = (
        db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
    )
    if sector is None:
        return False
    # getattr: combat FakeSession sectors are often SimpleNamespace without defenses
    for entry in (getattr(sector, "defenses", None) or {}).get("docked_npc_ships") or []:
        if (
            entry.get("ship_id") == str(ship_id)
            and entry.get("status") == DOCKED_OFF_DUTY
        ):
            return True
    return False


def sync_loop_c_occupancy(db: Session) -> Dict[str, int]:
    """Sleep/wake occupancy pass for Loop C.

    SLEEP NPCs with a home lodging enter occupancy; non-SLEEP NPCs still
    listed as occupants leave. Idempotent.
    """
    entered = 0
    left = 0
    sleepers = (
        db.query(NPCCharacter)
        .filter(NPCCharacter.current_activity == NPCActivity.SLEEP)
        .filter(
            (NPCCharacter.home_barracks_id.isnot(None))
            | (NPCCharacter.home_outlaw_base_id.isnot(None))
        )
        .all()
    )
    for npc in sleepers:
        if enter_sleep(db, npc):
            entered += 1

    awake = (
        db.query(NPCCharacter)
        .filter(NPCCharacter.current_activity != NPCActivity.SLEEP)
        .filter(
            (NPCCharacter.home_barracks_id.isnot(None))
            | (NPCCharacter.home_outlaw_base_id.isnot(None))
        )
        .all()
    )
    for npc in awake:
        lodging, _ = resolve_home_lodging(db, npc)
        if lodging is None:
            continue
        if _npc_id_str(npc.id) in (lodging.assigned_npc_ids or []):
            if leave_sleep(db, npc):
                left += 1

    # Keep Sector lodging flags aligned with OutlawBase / sector-location
    # NPCBarracks rows (WO-BUILD-SECTOR-IS-OUTLAW-ZONE-IS-NPC-BARRACKS-SECTOR).
    flag_stats = sync_sector_lodging_flags(db)
    return {
        "entered": entered,
        "left": left,
        "lodging_flags_checked": flag_stats["sectors_checked"],
        "lodging_flags_updated": flag_stats["sectors_updated"],
    }
