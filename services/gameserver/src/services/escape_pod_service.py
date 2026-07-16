"""
Escape Pod stranding egress -- the FREE (zero fuel, zero turns, zero
reputation) recovery mechanic from a WARP_SINK, at the cost of the ship
itself.

Canon: Max's design direction (WO-GWQ-STRANDING-2, 2026-07-10) -- "[stranded
players] have to escape pod out of there, as the escape pod uses no fuel";
abandoning the ship is the free path's real cost, contrasted with the
Slipdrive's now fuel-commodity-denominated self-rescue (slipdrive_service.py)
and the Federation distress beacon's reputation cost (distress_service.py).

[NO-CANON] This is a THIRD stranding-recovery mechanism; FEATURES/galaxy/
sectors.md § "Recovery from one-way stranding" currently documents only two
("two mechanisms ship in v1": Slipdrive, distress beacon). Flagged for
canon staging, not silently added to the doc by this WO.

Unlike combat_service._handle_ship_destruction (which marks the abandoned
hull `is_destroyed = True` and pays out insurance), this mechanism does
NOT destroy the ship -- it is a voluntary abandonment. The hull remains in
its current sector, undestroyed, marked `is_abandoned` / `abandoned_at`
(canon field pair, DATA_MODELS/ships.md:21,51-52 -- previously reserved for
the port-abandonment/free-claim feature, not yet built; this WO is a second
producer of the same state marker, not a competing concept). Cargo stays
WITH the abandoned hull (this is a calm, voluntary abandonment, not an
emergency hull-breach -- combat_service's 10%-rescue-to-pod convention does
not apply here; the abandoned ship's own cargo is part of what makes it
worth recovering).

Reuses ShipService._ensure_escape_pod (the same pod-reuse-or-create
machinery combat destruction already uses) rather than duplicating pod
creation. Target selection mirrors slipdrive_service.py exactly: UNDIRECTED
hop distance over sector_warps to the nearest sector with directed
out-degree >= 1 (NOT a WARP_SINK).

Available on ANY hull, from ANY sector -- matches the distress beacon's own
"not gated on actually being stranded" philosophy: canon frames the cost
(losing the ship) as flavor for why a player would only do this when truly
stranded, not a hard precondition the server enforces. A player who fires
it while not stranded just abandons a working ship for nothing -- their
call.
"""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.sector import Sector, sector_warps
from src.models.ship import ShipStatus, ShipType
from src.services.ship_service import ShipService

logger = logging.getLogger(__name__)


class EscapePodError(Exception):
    """Raised for player-facing escape-pod failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 4xx."""


# --- time helpers (mirrors slipdrive_service/distress_service) ---

def _now() -> datetime:
    return datetime.now(UTC)


# --- graph helpers (private to this service -- mirrors slipdrive_service.py
# / distress_service.py's own convention: each stranding-recovery mechanic
# owns its own graph helpers rather than a shared module) ---

def _load_sector_graph(
    db: Session,
) -> Tuple[List[Any], Dict[uuid.UUID, List[uuid.UUID]], Set[uuid.UUID]]:
    """Identical to slipdrive_service._load_sector_graph -- full-galaxy
    sector projection + UNDIRECTED adjacency + the set of sector PKs with
    directed out-degree 0 (WARP_SINK topology). Duplicated rather than
    imported: matches this codebase's established per-service graph-helper
    convention (quantum_service / slipdrive_service / distress_service each
    own their own copy), not a shared-module refactor this WO doesn't own."""
    sectors = db.query(
        Sector.id, Sector.sector_id, Sector.region_id, Sector.name,
    ).all()
    edges = db.query(
        sector_warps.c.source_sector_id,
        sector_warps.c.destination_sector_id,
        sector_warps.c.is_bidirectional,
    ).all()
    adjacency: Dict[uuid.UUID, List[uuid.UUID]] = {}
    has_outbound: Set[uuid.UUID] = set()
    for row in edges:
        adjacency.setdefault(row.source_sector_id, []).append(row.destination_sector_id)
        adjacency.setdefault(row.destination_sector_id, []).append(row.source_sector_id)
        has_outbound.add(row.source_sector_id)
        if row.is_bidirectional:
            has_outbound.add(row.destination_sector_id)
    sink_pks = {s.id for s in sectors} - has_outbound
    return sectors, adjacency, sink_pks


def _bfs_nearest(
    start_pk: uuid.UUID,
    adjacency: Dict[uuid.UUID, List[uuid.UUID]],
    matches,
    id_of,
) -> Optional[Tuple[uuid.UUID, int]]:
    """Identical shape to slipdrive_service._bfs_nearest / distress_service.
    _bfs_nearest. Includes `start_pk` at hop 0 -- firing from a non-sink
    sector is a harmless no-op teleport, not a special case."""
    if matches(start_pk):
        return start_pk, 0
    visited = {start_pk}
    frontier = [start_pk]
    hop = 0
    while frontier:
        hop += 1
        next_frontier: List[uuid.UUID] = []
        for node in frontier:
            for neighbor in adjacency.get(node, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        matched = [n for n in next_frontier if matches(n)]
        if matched:
            return min(matched, key=id_of), hop
        frontier = next_frontier
    return None


# --- shared validation ---

def _lock_player(db: Session, player_id: uuid.UUID) -> Player:
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not player:
        raise EscapePodError("Player not found")
    return player


# --- eject ---

def eject_to_escape_pod(
    db: Session, player_id: uuid.UUID, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Abandon the current ship for a free Escape Pod. Zero fuel, zero turns,
    zero reputation cost -- the cost IS the abandoned ship. Teleports to
    the nearest non-sink sector by undirected hop distance (same target
    algorithm as the Slipdrive), ignoring warp topology for the jump
    itself, because the whole point is escaping a sink no graph-legal move
    could ever leave.

    The abandoned hull stays behind, undestroyed, at its CURRENT sector
    (not the destination) -- marked `is_abandoned` / `abandoned_at` so it
    persists as a recoverable derelict. Its cargo is untouched (stays with
    the hull). FLUSH-ONLY -- the route owns the commit.
    """
    now = now or _now()
    player = _lock_player(db, player_id)

    ship = player.current_ship
    if not ship or ship.is_destroyed:
        raise EscapePodError("No active ship selected")
    if ship.type == ShipType.ESCAPE_POD:
        raise EscapePodError("You are already piloting an Escape Pod")
    if player.is_docked:
        raise EscapePodError("You cannot eject to an Escape Pod while docked -- launch first")
    if player.is_landed:
        raise EscapePodError("You cannot eject to an Escape Pod on a planet surface -- lift off first")
    if ship.status == ShipStatus.HARMONIZING:
        raise EscapePodError(
            "This ship is anchored to a beacon and harmonizing -- it cannot be abandoned"
        )

    sectors, adjacency, sink_pks = _load_sector_graph(db)
    pk_to_sector_id = {s.id: s.sector_id for s in sectors}
    by_pk = {s.id: s for s in sectors}
    origin = next((s for s in sectors if s.sector_id == player.current_sector_id), None)
    if origin is None:
        raise EscapePodError("Current sector has no charted coordinates")

    result = _bfs_nearest(
        origin.id, adjacency, lambda pk: pk not in sink_pks, lambda pk: pk_to_sector_id[pk]
    )
    if result is None:
        raise EscapePodError(
            "no_recovery_target: no non-sink sector was found to route the Escape Pod to"
        )
    destination_pk, hops = result
    destination = by_pk[destination_pk]

    # Abandon the CURRENT hull in place -- undestroyed, cargo untouched.
    abandoned_ship = ship
    abandoned_ship.is_abandoned = True
    abandoned_ship.abandoned_at = now
    abandoned_sector_id = abandoned_ship.sector_id
    abandoned_ship_id = abandoned_ship.id
    abandoned_ship_name = abandoned_ship.name

    # Reuse the SAME pod-reuse-or-create machinery combat destruction uses
    # (ShipService._ensure_escape_pod) -- the pod spawns at the origin
    # (sink) sector, matching the piloted-ejection precedent, then the
    # player's arrival teleport below moves it (and the player) onward.
    escape_pod = ShipService(db)._ensure_escape_pod(player, origin.sector_id)
    player.current_ship_id = escape_pod.id
    from src.services.ship_service import sync_current_pilot
    sync_current_pilot(player, escape_pod, old_ship=abandoned_ship)  # QUEUE-REGISTRY-PILOT-WIRING

    # Teleport arrival -- mirrors slipdrive_service.complete_charge /
    # distress_service.use_distress_beacon's own player-state sync (sector,
    # region, undock/unland flags, pod's sector, presence broadcast)
    # WITHOUT any adjacency requirement: bypasses the warp graph, same as
    # the other two mechanisms, because the whole point is escaping a sink
    # no real move could ever traverse.
    old_sector_id = player.current_sector_id
    if destination.sector_id != old_sector_id:
        player.current_sector_id = destination.sector_id
        player.current_region_id = destination.region_id
        player.is_docked = False
        player.is_landed = False
        player.current_port_id = None
        player.current_planet_id = None
        from src.services.docking_service import release as _release_docking_slip
        _release_docking_slip(db, None, player)
        escape_pod.sector_id = destination.sector_id

        from src.services.movement_service import MovementService
        MovementService(db)._update_player_presence(player, old_sector_id, destination.sector_id)
    else:
        escape_pod.sector_id = destination.sector_id

    db.flush()  # route owns the commit

    logger.info(
        "Player %s abandoned ship %s at sector %s (now derelict) and escaped "
        "via pod: sector %s -> %s (%d hops)",
        player.id, abandoned_ship_name, abandoned_sector_id,
        old_sector_id, destination.sector_id, hops,
    )

    return {
        "outcome": "escape_pod_ejection",
        "abandoned_ship_id": str(abandoned_ship_id),
        "abandoned_ship_name": abandoned_ship_name,
        "abandoned_at_sector_id": abandoned_sector_id,
        "destination_sector_id": destination.sector_id,
        "destination_name": destination.name,
        "hops": hops,
        "fuel_spent": 0,
        "turns_spent": 0,
        "reputation_delta": 0,
    }
