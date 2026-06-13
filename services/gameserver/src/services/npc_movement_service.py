"""NPC Movement Service — single-hop NPC moves on the player warp graph.

Canon anchors:
  - SYSTEMS/npc-scheduler.md "NPC movement": NPCs move on the same warp
    graph players use and pay the same ``turn_cost``; they have no player
    turn pools — the scheduler is the authoritative actor — but the TIME
    a movement takes is honored (~86 wall-clock seconds per turn at
    L1-equivalent regen pacing, scaled through GAME_TIME_SCALE).
  - ADR-0034: NPCs respect one-way warps (the directional helpers below
    only traverse edges a player could traverse in the same direction).
  - ADR-0060 G-V3: no quantum-jump pursuit — NPCs never use QJ here.

Scope decisions (documented, not invented):
  - NPCs do NOT traverse player-built warp gates (ARTIFICIAL tunnels
    with created_by_player_id set) — canon is silent on NPC use of
    player infrastructure, so the conservative reading applies; flagged
    as a pending decision in the docs repo.

CONCURRENCY — lock order (global convention for NPC writers):
    Player → Station → Ship → NPCCharacter → Sector (ascending sector_id)

``move_npc`` locks the NPC's SHIP row first, then both sector rows in
ascending ``sector_id`` order. This matches the combat path
(combat_service.attack_npc_ship locks Player then the NPC Ship, and
handle_npc_ship_destroyed then locks the Sector) so the two paths can
never deadlock AB-BA. Liveness is re-validated AFTER the ship lock is
held: a move that raced a kill must not resurrect presence the KIA
handler just cleaned.
"""

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import canonical_hours_since
from src.models.npc_character import NPCCharacter, NPCStatus, NPCArchetype
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.movement_service import MovementService, _is_player_gate
from src.services.npc_spawn_service import _presence_entry, _patrol_route

logger = logging.getLogger(__name__)

# Canon (SYSTEMS/npc-scheduler.md commute feasibility): wall-clock pacing
# of NPC movement is ~86 seconds per turn at L1-equivalent regen pacing.
SECONDS_PER_TURN_L1 = 86

# Statuses that may never be moved by the scheduler.
_UNMOVABLE_STATUSES = (
    NPCStatus.KIA,
    NPCStatus.RESPAWNING,
    NPCStatus.RETIRED,
    NPCStatus.REASSIGNED,
)


def hop_cost(db: Session, origin_sector_id: int, dest_sector_id: int,
             ship: Optional[Ship]) -> Optional[int]:
    """Turn cost of a single legal hop origin → dest, or None when no
    NPC-traversable connection exists.

    Direct warps (including reverse traversal of bidirectional rows) and
    natural warp tunnels qualify; player-built warp gates do not (see
    module docstring).
    """
    ms = MovementService(db)
    can_warp, warp_cost, _ = ms._check_direct_warp(
        origin_sector_id, dest_sector_id, ship
    )
    if can_warp:
        return warp_cost

    origin = db.query(Sector).filter(Sector.sector_id == origin_sector_id).first()
    dest = db.query(Sector).filter(Sector.sector_id == dest_sector_id).first()
    if origin is None or dest is None:
        return None

    tunnel = (
        db.query(WarpTunnel)
        .filter(
            WarpTunnel.origin_sector_id == origin.id,
            WarpTunnel.destination_sector_id == dest.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE,
        )
        .first()
    )
    if tunnel is None:
        tunnel = (
            db.query(WarpTunnel)
            .filter(
                WarpTunnel.origin_sector_id == dest.id,
                WarpTunnel.destination_sector_id == origin.id,
                WarpTunnel.is_bidirectional == True,  # noqa: E712
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
            )
            .first()
        )
    if tunnel is None or _is_player_gate(tunnel):
        return None

    cost = tunnel.turn_cost or 1
    if ship is not None and getattr(ship, "warp_capable", False):
        cost = max(1, int(cost * 0.8))
    return max(1, cost)


def add_npc_presence(sector: Sector, npc: NPCCharacter, ship: Ship) -> None:
    """Append the NPC's players_present entry (idempotent). Caller must
    hold the sector row lock."""
    npc_id = str(npc.id)
    players_present = [
        p for p in (sector.players_present or [])
        if p.get("player_id") != npc_id
    ]
    players_present.append(_presence_entry(npc, ship))
    sector.players_present = players_present
    flag_modified(sector, "players_present")


def remove_npc_presence(sector: Sector, npc_id: uuid.UUID) -> None:
    """Drop the NPC's players_present entry. Caller must hold the sector
    row lock."""
    npc_id_str = str(npc_id)
    sector.players_present = [
        p for p in (sector.players_present or [])
        if p.get("player_id") != npc_id_str
    ]
    flag_modified(sector, "players_present")


def _locked_sectors(db: Session, sector_ids: List[int]) -> Dict[int, Sector]:
    """Lock sector rows in ascending sector_id order (deadlock-safe both
    against other NPC movers and against the KIA path)."""
    locked: Dict[int, Sector] = {}
    for sid in sorted(set(sector_ids)):
        row = (
            db.query(Sector)
            .filter(Sector.sector_id == sid)
            .with_for_update()
            .first()
        )
        if row is not None:
            locked[sid] = row
    return locked


def move_npc(
    db: Session,
    npc: NPCCharacter,
    dest_sector_id: int,
    *,
    enforce_pacing: bool = True,
) -> List[Dict[str, Any]]:
    """Move an NPC one hop to ``dest_sector_id``.

    Returns realtime event dicts for the caller to broadcast AFTER its
    commit (npc_left_sector / npc_entered_sector), or [] when the move
    did not happen (no connection, pacing window not yet elapsed, NPC no
    longer movable). Flush-only — the caller owns the transaction.
    """
    if npc.ship_id is None or npc.current_sector_id is None:
        return []
    origin_sector_id = npc.current_sector_id
    if origin_sector_id == dest_sector_id:
        return []

    # LOCK 1 — the NPC's ship row (before any sector rows; see module
    # docstring lock order).
    ship = (
        db.query(Ship)
        .filter(Ship.id == npc.ship_id)
        .with_for_update()
        .first()
    )
    if ship is None or ship.is_destroyed:
        return []

    # Liveness re-validation under the ship lock — a kill that raced this
    # move has already flipped status / cleaned presence; do not undo it.
    db.refresh(npc)
    if npc.status in _UNMOVABLE_STATUSES or npc.current_sector_id != origin_sector_id:
        return []

    cost = hop_cost(db, origin_sector_id, dest_sector_id, ship)
    if cost is None:
        logger.warning(
            "NPC %s: no traversable connection %s -> %s",
            npc.id, origin_sector_id, dest_sector_id,
        )
        return []

    # Canon pacing: a movement takes cost × ~86s of canonical time;
    # last_seen_at marks the previous movement/spawn.
    if enforce_pacing and npc.last_seen_at is not None:
        required_hours = cost * SECONDS_PER_TURN_L1 / 3600.0
        if canonical_hours_since(npc.last_seen_at) < required_hours:
            return []

    # LOCK 2 — both sector rows, ascending sector_id.
    sectors = _locked_sectors(db, [origin_sector_id, dest_sector_id])
    dest = sectors.get(dest_sector_id)
    if dest is None:
        return []
    origin = sectors.get(origin_sector_id)

    now = datetime.now(UTC)
    if origin is not None:
        remove_npc_presence(origin, npc.id)
    add_npc_presence(dest, npc, ship)

    npc.current_sector_id = dest_sector_id
    npc.home_region_id = npc.home_region_id or dest.region_id
    npc.last_seen_at = now
    ship.sector_id = dest_sector_id

    db.flush()

    base = {
        "npc_id": str(npc.id),
        "display_name": npc.display_name,
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.name,
        "is_npc": True,
        "timestamp": now.isoformat(),
    }
    return [
        {**base, "type": "npc_left_sector", "sector_id": origin_sector_id,
         "destination_sector_id": dest_sector_id},
        {**base, "type": "npc_entered_sector", "sector_id": dest_sector_id,
         "origin_sector_id": origin_sector_id},
    ]


def next_hop_toward(db: Session, origin_sector_id: int,
                    target_sector_id: int) -> Optional[int]:
    """First hop of the shortest path origin → target on the warp graph
    (BFS via MovementService), or None when unreachable."""
    if origin_sector_id == target_sector_id:
        return None
    path = MovementService(db).get_path_between_sectors(
        origin_sector_id, target_sector_id
    )
    if len(path) < 2:
        return None
    return int(path[1]["sector_id"])


# ---------------------------------------------------------------------------
# Stranded-NPC repair
# ---------------------------------------------------------------------------

def _collect_sector_ids(obj: Any, out: set) -> None:
    """Recursively gather every ``sector_id`` int and ``sectors`` list entry
    referenced anywhere in a daily_schedule JSON blob."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "sector_id" and isinstance(v, int):
                out.add(v)
            elif k == "sectors" and isinstance(v, list):
                out.update(x for x in v if isinstance(x, int))
            else:
                _collect_sector_ids(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _collect_sector_ids(x, out)


def _schedule_target_sectors(npc: NPCCharacter) -> set:
    out: set = set()
    _collect_sector_ids(npc.daily_schedule or {}, out)
    return out


def _relocate_npc(db: Session, npc: NPCCharacter, dest_sector_id: int) -> bool:
    """Teleport-repair a stranded NPC to ``dest_sector_id`` (no warp-link,
    cost or pacing — this fixes a frozen NPC, it is not a normal move).
    Same lock order as ``move_npc`` (ship row, then both sectors ascending)
    so it can't deadlock against movers or the KIA path. Returns True when
    the NPC was relocated. Flush-only; caller owns the commit."""
    if npc.ship_id is None or npc.current_sector_id is None:
        return False
    origin_id = npc.current_sector_id
    if origin_id == dest_sector_id:
        return False
    ship = db.query(Ship).filter(Ship.id == npc.ship_id).with_for_update().first()
    if ship is None or ship.is_destroyed:
        return False
    db.refresh(npc)
    if npc.status in _UNMOVABLE_STATUSES or npc.current_sector_id != origin_id:
        return False
    sectors = _locked_sectors(db, [origin_id, dest_sector_id])
    dest = sectors.get(dest_sector_id)
    if dest is None:  # destination sector must exist
        return False
    origin = sectors.get(origin_id)
    if origin is not None:
        remove_npc_presence(origin, npc.id)
    add_npc_presence(dest, npc, ship)
    npc.current_sector_id = dest_sector_id
    npc.home_region_id = npc.home_region_id or dest.region_id
    npc.last_seen_at = datetime.now(UTC)
    ship.sector_id = dest_sector_id
    db.flush()
    return True


def relocate_stranded_npcs(db: Session) -> int:
    """Find NPCs frozen because their current sector can't reach their
    schedule's target sectors (the silent ``next_hop_toward``→None no-op that
    leaves a trader stuck in the wrong region forever) and teleport-repair
    each onto one of its own route sectors so it can resume moving.

    An NPC is considered fine (skipped) when its current sector IS a target,
    or when ANY target is reachable from it (it's simply en route). Idempotent:
    a healthy NPC is never touched. Flush-only; caller owns the commit."""
    movable = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status.notin_(_UNMOVABLE_STATUSES),
            NPCCharacter.ship_id.isnot(None),
            NPCCharacter.current_sector_id.isnot(None),
        )
        .all()
    )
    msvc = MovementService(db)
    relocated = 0
    for npc in movable:
        targets = _schedule_target_sectors(npc)
        if not targets or npc.current_sector_id in targets:
            continue  # no schedule, or already on its route
        cur = npc.current_sector_id
        reachable = any(
            len(msvc.get_path_between_sectors(cur, t)) >= 2
            for t in sorted(targets)
        )
        if reachable:
            continue  # en route — it will arrive on its own
        dest = min(targets)  # deterministic anchor on its own route
        if _relocate_npc(db, npc, dest):
            relocated += 1
            logger.info("Relocated stranded NPC %s: sector %s -> %s",
                        npc.id, cur, dest)
    return relocated


def disperse_law_patrols(db: Session) -> int:
    """Spread LAW_ENFORCEMENT NPCs across their region instead of swarming the
    roster's single host sector (24 Sentinels anchored to one sector + its two
    neighbours made ~6-8 pile into every sector around the host). Each LAW NPC
    gets a DETERMINISTIC scattered patrol anchor (seeded by its id) somewhere in
    its region, a small local patrol loop around that anchor, and is relocated
    there. Deterministic + idempotent: an NPC already on its scattered anchor is
    left untouched, so this self-heals re-clustered respawns each startup without
    churn. Pirates are intentionally NOT dispersed (their clustering is canon —
    holdings/strongholds). Flush-only; caller owns the commit."""
    law = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
            NPCCharacter.status.notin_(_UNMOVABLE_STATUSES),
            NPCCharacter.ship_id.isnot(None),
        )
        .all()
    )
    if not law:
        return 0
    region_sectors: Dict[Any, List[int]] = {}
    dispersed = 0
    for npc in law:
        region_id = npc.home_region_id
        if region_id is None:
            cur = db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
            region_id = cur.region_id if cur else None
        if region_id is None:
            continue
        if region_id not in region_sectors:
            region_sectors[region_id] = sorted(
                sid for (sid,) in db.query(Sector.sector_id)
                .filter(Sector.region_id == region_id).all()
            )
        sids = region_sectors[region_id]
        if not sids:
            continue
        # Deterministic per-NPC anchor (stable across restarts → idempotent).
        h = int(str(npc.id).replace("-", "")[:12], 16)
        anchor = sids[h % len(sids)]
        route = _patrol_route(db, anchor)
        cur_route = (
            ((npc.daily_schedule or {}).get("blocks") or [{}])[0]
            .get("location_ref") or {}
        ).get("sectors")
        if cur_route == route and npc.current_sector_id == anchor:
            continue  # already dispersed to its anchor
        # Relocate FIRST — _relocate_npc does db.refresh(npc), which would
        # discard an unflushed daily_schedule change; set the scattered route
        # AFTER so it survives (the caller commits).
        if npc.current_sector_id != anchor:
            _relocate_npc(db, npc, anchor)
        npc.daily_schedule = {
            "timezone": "utc",
            "shift_offset_hours": 0,
            "blocks": [{
                "start_minute": 0, "end_minute": 1440,
                "activity": "patrol", "location_type": "patrol_route",
                "location_ref": {"sectors": route, "minutes_per_sector": 240},
            }],
        }
        npc.home_region_id = npc.home_region_id or region_id
        dispersed += 1
    return dispersed
