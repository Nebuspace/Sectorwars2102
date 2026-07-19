"""
Federation Distress Beacon -- the universal one-way-stranding panic button.

Canon: sw2102-docs FEATURES/galaxy/sectors.md § "Recovery from one-way
stranding" ("Federation distress beacon -- free transport to the nearest
fedspace sector, at the cost of -10 Terran Federation reputation per use,
with a 24-hour cooldown. The panic button."), cross-referenced from
FEATURES/gameplay/movement.md § "Cross-region travel" #3 and ADR-0034
(one-way warp design). The exact reputation trigger row is already
canonical in FEATURES/gameplay/factions-and-teams.md § reputation-triggers:
"Use the Federation distress beacon (one-way-stranding rescue) | -10 / use
| 24-hour cooldown; free transport to nearest fedspace sector."

Available on ANY hull (unlike the Warp Jumper-exclusive Slipdrive -- see
slipdrive_service.py) and from ANY sector, not gated on actually being
stranded: canon frames the -10 rep hit as "the panic-button cost for being
trapped behind a one-way warp", flavor for why the cost exists, not a hard
precondition the server enforces. A player who fires it while not stranded
just pays -10 rep for a free ride to fedspace -- harmless, their call.

"Free transport" means zero turns, zero fuel/cargo, zero credits -- only
the reputation delta and the cooldown. It is a TELEPORT, not a graph-legal
move: the whole point is escaping a WARP_SINK sector with zero outbound
warps, which a real MovementService traversal could never reach a fedspace
sector from. Target selection is UNDIRECTED hop distance over sector_warps
(direction/is_bidirectional deliberately ignored -- it is used purely as a
distance metric here, never to authorize the actual jump).
"""
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.core.game_time import scaled_deadline
from src.models.faction import FactionType
from src.models.player import Player
from src.models.sector import Sector, sector_warps
from src.models.ship import ShipStatus
from src.services.faction_service import apply_faction_rep_delta

logger = logging.getLogger(__name__)


class DistressError(Exception):
    """Raised for player-facing distress-beacon failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 4xx.
    `payload` carries machine-readable extras (e.g. cooldown remaining)
    the route merges into the error response body. `status_code` defaults
    to 400; the cooldown-violation raise sets 429 (rate-limit semantics)."""

    def __init__(
        self, detail: str, payload: Optional[Dict[str, Any]] = None, status_code: int = 400,
    ):
        super().__init__(detail)
        self.payload = payload or {}
        self.status_code = status_code


# --- Canonical constants (factions-and-teams.md § reputation-triggers) ---

DISTRESS_REP_DELTA = -10
DISTRESS_COOLDOWN_HOURS = 24.0  # canonical, scaled via scaled_deadline
DISTRESS_REASON = "distress_beacon_use"


# --- time helpers (mirrors quantum_service's _now/_aware/_cooldown_active) ---

def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _cooldown_until(last_distress_at: Optional[datetime]) -> Optional[datetime]:
    """The 24h canonical deadline derived from the LAST FIRE timestamp --
    never stored pre-computed (the column is a fire-time anchor, matching
    its `last_distress_at` name)."""
    last = _aware(last_distress_at)
    if last is None:
        return None
    return scaled_deadline(DISTRESS_COOLDOWN_HOURS, start=last)


def _cooldown_active(until: Optional[datetime], now: Optional[datetime] = None) -> bool:
    until = _aware(until)
    now = now or _now()
    return bool(until and until > now)


# --- graph helpers (private to this service -- mirrors quantum_service's
# own private _load_sector_points/_inter_sector_spacing convention: each
# service owns its own geometry/graph helpers rather than a shared module) ---

def _load_sector_graph(db: Session) -> Tuple[List[Any], Dict[uuid.UUID, List[uuid.UUID]]]:
    """Full-galaxy sector projection + UNDIRECTED adjacency over
    sector_warps (mirrors quantum_service._load_sector_points's full-table
    load -- same accepted performance envelope). Direction/is_bidirectional
    is deliberately ignored: this graph is a pure hop-distance metric for
    "nearest reachable sector", never a traversal-legality check."""
    sectors = db.query(
        Sector.id, Sector.sector_id, Sector.region_id, Sector.name, Sector.special_features,
    ).all()
    edges = db.query(
        sector_warps.c.source_sector_id, sector_warps.c.destination_sector_id,
    ).all()
    adjacency: Dict[uuid.UUID, List[uuid.UUID]] = {}
    for row in edges:
        adjacency.setdefault(row.source_sector_id, []).append(row.destination_sector_id)
        adjacency.setdefault(row.destination_sector_id, []).append(row.source_sector_id)
    return sectors, adjacency


def _bfs_nearest(
    start_pk: uuid.UUID,
    adjacency: Dict[uuid.UUID, List[uuid.UUID]],
    matches,
    id_of,
) -> Optional[Tuple[uuid.UUID, int]]:
    """BFS from `start_pk`; returns (nearest matching sector PK, hop count)
    for the first `matches(pk)` hit, level by level. Includes `start_pk`
    itself at hop 0 (a beacon fired FROM fedspace is a harmless no-op
    teleport, not a special case). Ties within the same hop level are
    broken deterministically by the LOWEST `id_of(pk)` (the game-facing
    Sector.sector_id)."""
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
        raise DistressError("Player not found")
    return player


# --- status ---

def get_status(db: Session, player: Player, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read-only beacon status for the recovery console. Never raises --
    a player who has never used the beacon (last_distress_at NULL) reads
    as immediately available."""
    now = now or _now()
    until = _cooldown_until(getattr(player, "last_distress_at", None))
    active = _cooldown_active(until, now)
    return {
        "available": not active,
        "cooldown_until": until.isoformat() if active else None,
        "last_used_at": _aware(getattr(player, "last_distress_at", None)).isoformat()
        if getattr(player, "last_distress_at", None) else None,
    }


# --- use ---

def use_distress_beacon(
    db: Session, player_id: uuid.UUID, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fire the Federation distress beacon: free (zero turn/fuel/credit
    cost) teleport to the nearest fedspace sector by undirected hop
    distance, -10 Terran Federation reputation, 24h scaled cooldown.

    Row-locks the player FOR UPDATE (serializes concurrent double-fire by
    the same player) before re-checking the cooldown, so two racing calls
    resolve to exactly one success. FLUSH-ONLY -- the route owns the
    commit (harvest_nebula precedent in quantum_service.py)."""
    now = now or _now()
    player = _lock_player(db, player_id)

    ship = player.current_ship
    if not ship or ship.is_destroyed:
        raise DistressError("No active ship selected")
    if ship.status == ShipStatus.HARMONIZING:
        raise DistressError(
            "This ship is anchored to a beacon and harmonizing -- it cannot fire a distress beacon"
        )
    if player.is_docked:
        raise DistressError("You cannot fire a distress beacon while docked -- launch first")
    if player.is_landed:
        raise DistressError("You cannot fire a distress beacon on a planet surface -- lift off first")

    until = _cooldown_until(getattr(player, "last_distress_at", None))
    if _cooldown_active(until, now):
        raise DistressError(
            f"The distress beacon is recharging until {until.isoformat()}",
            payload={
                "cooldown_until": until.isoformat(),
                "remaining_seconds": max(0.0, (until - now).total_seconds()),
            },
            status_code=429,
        )

    sectors, adjacency = _load_sector_graph(db)
    pk_to_sector_id = {s.id: s.sector_id for s in sectors}
    by_pk = {s.id: s for s in sectors}
    origin = next((s for s in sectors if s.sector_id == player.current_sector_id), None)
    if origin is None:
        raise DistressError("Current sector has no charted coordinates")

    def _is_fedspace(pk: uuid.UUID) -> bool:
        sector = by_pk.get(pk)
        return bool(sector and "fedspace" in (sector.special_features or []))

    result = _bfs_nearest(origin.id, adjacency, _is_fedspace, lambda pk: pk_to_sector_id[pk])
    if result is None:
        raise DistressError(
            "no_fedspace: the distress beacon found no fedspace sector to route you to"
        )
    destination_pk, hops = result
    destination = by_pk[destination_pk]

    # Reputation hit -- via the sync flush-only helper (never the async
    # FactionService.update_reputation, which commits internally). Never
    # raises: a missing Federation faction row logs an error and degrades
    # to a lost rep delta rather than blocking the rescue.
    apply_faction_rep_delta(db, player.id, FactionType.FEDERATION, DISTRESS_REP_DELTA, DISTRESS_REASON)

    # Teleport arrival -- mirrors quantum_service.jump()'s player-state sync
    # (sector, region, undock/unland flags, ship sector, presence broadcast)
    # WITHOUT any adjacency requirement: this bypasses the warp graph, same
    # as a quantum jump, because the whole point is escaping a sink no real
    # move could ever traverse.
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
        ship.sector_id = destination.sector_id

        from src.services.movement_service import MovementService
        MovementService(db)._update_player_presence(player, old_sector_id, destination.sector_id)

    player.last_distress_at = now

    db.flush()  # route owns the commit

    logger.info(
        "Player %s fired the distress beacon: sector %s -> %s (%d hops, rep %+d)",
        player.id, old_sector_id, destination.sector_id, hops, DISTRESS_REP_DELTA,
    )

    return {
        "destination_sector_id": destination.sector_id,
        "destination_name": destination.name,
        "hops": hops,
        "reputation_delta": DISTRESS_REP_DELTA,
        "cooldown_until": _cooldown_until(player.last_distress_at).isoformat(),
    }
