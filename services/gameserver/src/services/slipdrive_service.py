"""
Warp Jumper Slipdrive -- the quantum_jump_capable hull's stranded-recovery
self-rescue primitive (renamed from TransWarp).

Canon: sw2102-docs FEATURES/galaxy/sectors.md § "Recovery from one-way
stranding" ("Slipdrive (Warp Jumper module) -- multi-turn charge, fuel cost
scaled by graph distance to the nearest non-sink sector. Self-rescue."),
FEATURES/gameplay/movement.md § "Cross-region travel" #3, FEATURES/gameplay/
ships.md ("the Slipdrive ... handles stranded recovery from WARP_SINK traps
and other one-way dead-ends"). Complementary to (never redundant with)
Quantum Jump: Quantum Jump is the discovery primitive and does NOT bypass
one-ways for transit (would devalue the WJ's combat-mobility role);
Slipdrive is the escape primitive and explicitly does.

Two-phase commit (multi-turn charge, then completion), mirroring the
HARMONIZING spin-up shape used elsewhere in this codebase (a turn cost paid
up front + a real-time-scaled deadline before the action resolves). Charge
state persists in ``Ship.equipment_slots["slipdrive_charge"]`` -- a
DELIBERATELY DISTINCT key from the ``"slipdrive"`` key DATA_MODELS/ships.md:71
reserves ("Holds slot-keyed equipment installs including quantum_harvester,
tractor_beam, slipdrive, and sensor"): that key names whether the Slipdrive
MODULE is physically installed (an equip-tier fact, mirroring
quantum_harvester/tractor_beam/sensor), which is a distinct, not-yet-built
feature from this WO's transient per-charge state. Reusing "slipdrive" for
charge state would collide with that future equip-module install the moment
it ships. This WO ships with ZERO new Ship columns either way.

Movement mid-charge invalidates it LAZILY: begin_charge snapshots the
player's current sector; complete_charge re-checks it against the player's
CURRENT sector before resolving. A mismatch means ordinary movement
happened since -- the charge is cancelled, no refund of the turns already
spent (this needs no hook into movement_service.py at all).

Target selection: UNDIRECTED hop distance over sector_warps to the nearest
sector with directed out-degree >= 1 (i.e. NOT a WARP_SINK by the
topological definition in DATA_MODELS/special-formations.md). Completion
teleports there directly, ignoring warp topology for the jump itself --
same as Quantum Jump's own arrival execution -- because the whole point is
escaping a sink no graph-legal move could ever leave.
"""
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import scaled_deadline
from src.models.player import Player
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipSpecification, ShipStatus
from src.services.turn_service import regenerate_turns, spend_turns

logger = logging.getLogger(__name__)


class SlipdriveError(Exception):
    """Raised for player-facing Slipdrive failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 4xx."""


# --- Canonical constants ---
# NO-CANON magnitudes (canon names the mechanism, not these numbers --
# FEATURES/galaxy/sectors.md only says "multi-turn charge, fuel cost scaled
# by graph distance"): flagged for Max's sign-off. SLIPDRIVE_CHARGE_HOURS
# mirrors the closest existing canon analog for a module spin-up window
# (ADR-0036 gate-construction HARMONIZING is 1h canonical) at half that,
# since this is emergency self-rescue, not a construction ritual.
#
# WO-GWQ-STRANDING-2 [NO-CANON] DENOMINATION CHANGE: SLIPDRIVE_FUEL_BASE /
# SLIPDRIVE_FUEL_PER_HOP now denominate the ship.cargo["contents"]["fuel"]
# COMMODITY (units), not player.credits (currency) -- Max's design
# direction: the Slipdrive burns real fuel cargo; only the escape pod is
# free. The MAGNITUDES (50 base + 10/hop) are UNCHANGED from the prior
# WO-GWQ-STRANDING credits framing -- only what they measure changed, per
# the dispatch's explicit "[NO-CANON] flag the denomination + the numbers"
# instruction. Re-flagged for Max's sign-off under the new unit.
SLIPDRIVE_CHARGE_TURN_COST = 3
SLIPDRIVE_CHARGE_HOURS = 0.5  # canonical, scaled via scaled_deadline
SLIPDRIVE_FUEL_BASE = 50
SLIPDRIVE_FUEL_PER_HOP = 10
FUEL_COMMODITY_KEY = "fuel"

_EQUIPMENT_KEY = "slipdrive_charge"


# --- time helpers (mirrors quantum_service's _now/_aware/_cooldown_active) ---

def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


# --- graph helpers (private to this service -- mirrors quantum_service's
# own private _load_sector_points convention: each service owns its own
# geometry/graph helpers rather than a shared module) ---

def _load_sector_graph(
    db: Session,
) -> Tuple[List[Any], Dict[uuid.UUID, List[uuid.UUID]], Set[uuid.UUID]]:
    """Full-galaxy sector projection + UNDIRECTED adjacency + the set of
    sector PKs with directed out-degree 0 (WARP_SINK topology --
    DATA_MODELS/special-formations.md: "Vertex with directed in-degree >= 1
    and directed out-degree 0"). A bidirectional row contributes an
    outbound edge from BOTH endpoints; a one-way row (is_bidirectional=
    False) contributes an outbound edge from its source only -- mirrors
    movement_service's own "reverse direction only when is_bidirectional"
    convention. Adjacency itself stays direction-agnostic: it is a pure
    hop-distance metric, never a traversal-legality check."""
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
    """BFS from `start_pk`; returns (nearest matching sector PK, hop count)
    for the first `matches(pk)` hit, level by level, ties broken by the
    LOWEST `id_of(pk)` (the game-facing Sector.sector_id). Includes
    `start_pk` at hop 0 -- a Slipdrive fired from a non-sink sector is a
    harmless no-op teleport, not a special case."""
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


def _fuel_cost(hops: int) -> int:
    return SLIPDRIVE_FUEL_BASE + SLIPDRIVE_FUEL_PER_HOP * hops


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
        raise SlipdriveError("Player not found")
    return player


def _require_slipdrive_hull(db: Session, player: Player) -> Ship:
    """quantum_jump_capable hulls only -- checks the ShipSpecification
    flag rather than hardcoding ShipType.WARP_JUMPER (only hull with the
    flag today per ship_specifications_seeder.py, but this stays correct
    if a future hull ever gains it)."""
    ship = player.current_ship
    if not ship or ship.is_destroyed:
        raise SlipdriveError("No active ship selected")
    spec = db.query(ShipSpecification).filter(ShipSpecification.type == ship.type).first()
    if not spec or not spec.quantum_jump_capable:
        raise SlipdriveError("The Slipdrive is exclusive to quantum-jump-capable hulls")
    db.refresh(ship)
    return ship


def _slot(ship: Ship) -> Optional[Dict[str, Any]]:
    slots = ship.equipment_slots if isinstance(ship.equipment_slots, dict) else {}
    return slots.get(_EQUIPMENT_KEY)


def _set_slot(ship: Ship, value: Optional[Dict[str, Any]]) -> None:
    slots = dict(ship.equipment_slots) if isinstance(ship.equipment_slots, dict) else {}
    if value is None:
        slots.pop(_EQUIPMENT_KEY, None)
    else:
        slots[_EQUIPMENT_KEY] = value
    ship.equipment_slots = slots
    flag_modified(ship, "equipment_slots")


# --- status ---

def get_status(db: Session, player: Player, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read-only Slipdrive status for the recovery console. Never raises
    -- a non-quantum-jump-capable hull or a ship-less player simply reads
    as not-charging rather than erroring."""
    now = now or _now()
    ship = player.current_ship
    charge = _slot(ship) if ship else None
    if not charge:
        return {"charging": False, "charge_deadline": None, "ready": False}
    deadline = _aware(datetime.fromisoformat(charge["deadline"]))
    moved = charge.get("origin_sector_id") != player.current_sector_id
    return {
        "charging": not moved,
        "charge_deadline": deadline.isoformat(),
        "ready": (not moved) and now >= deadline,
        "cancelled_by_movement": moved,
    }


# --- begin ---

def begin_charge(
    db: Session, player_id: uuid.UUID, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Phase 1: spin up the Slipdrive. Debits SLIPDRIVE_CHARGE_TURN_COST
    turns immediately (non-refundable regardless of how the charge later
    resolves) and arms a scaled-time deadline before completion is valid.
    FLUSH-ONLY -- the route owns the commit."""
    now = now or _now()
    player = _lock_player(db, player_id)
    ship = _require_slipdrive_hull(db, player)

    if player.is_docked:
        raise SlipdriveError("You cannot charge the Slipdrive while docked -- launch first")
    if player.is_landed:
        raise SlipdriveError("You cannot charge the Slipdrive on a planet surface -- lift off first")
    if ship.status == ShipStatus.HARMONIZING:
        raise SlipdriveError("This Warp Jumper is anchored to a beacon and harmonizing -- it cannot charge the Slipdrive")

    existing = _slot(ship)
    if existing:
        moved = existing.get("origin_sector_id") != player.current_sector_id
        deadline = _aware(datetime.fromisoformat(existing["deadline"]))
        if not moved and now < deadline:
            raise SlipdriveError(f"The Slipdrive is already charging (ready at {deadline.isoformat()})")
        # Stale (invalidated by movement) or long-ready-but-uncompleted --
        # clear and let a fresh charge start rather than permanently
        # locking the player out.

    # THE FROZEN HOOK (turn_service.regenerate_turns docstring): every
    # turn-SPEND site calls this inside its existing row lock, before the
    # affordability check, so the pool reflects real elapsed time.
    regenerate_turns(db, player)
    if (player.turns or 0) < SLIPDRIVE_CHARGE_TURN_COST:
        raise SlipdriveError(
            f"Not enough turns to charge the Slipdrive. Need {SLIPDRIVE_CHARGE_TURN_COST}, have {player.turns or 0}"
        )

    spend_turns(player, SLIPDRIVE_CHARGE_TURN_COST)
    deadline = scaled_deadline(SLIPDRIVE_CHARGE_HOURS, start=now)
    _set_slot(ship, {
        "origin_sector_id": player.current_sector_id,
        "charge_started_at": now.isoformat(),
        "deadline": deadline.isoformat(),
    })

    db.flush()  # route owns the commit

    logger.info(
        "Player %s began Slipdrive charge at sector %s, ready %s",
        player.id, player.current_sector_id, deadline.isoformat(),
    )

    return {
        "charging": True,
        "charge_deadline": deadline.isoformat(),
        "turns_spent": SLIPDRIVE_CHARGE_TURN_COST,
        "turns_remaining": player.turns,
    }


# --- complete ---

def complete_charge(
    db: Session, player_id: uuid.UUID, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Phase 2: resolve a ready charge. Teleports to the nearest non-sink
    sector by undirected hop distance, ignoring warp topology for the jump
    itself (same as Quantum Jump's own arrival). Fuel is charged HERE, not
    at begin -- SLIPDRIVE_FUEL_BASE + SLIPDRIVE_FUEL_PER_HOP per hop, drawn
    from ship.cargo["contents"]["fuel"] (WO-GWQ-STRANDING-2: re-denominated
    from the prior WO-GWQ-STRANDING's player.credits framing -- the
    Slipdrive burns real fuel cargo the ship must be carrying, or have
    delivered; see fuel_delivery_service.py). FLUSH-ONLY -- the route owns
    the commit."""
    now = now or _now()
    player = _lock_player(db, player_id)
    ship = _require_slipdrive_hull(db, player)

    charge = _slot(ship)
    if not charge:
        raise SlipdriveError("No Slipdrive charge in progress")

    if charge.get("origin_sector_id") != player.current_sector_id:
        _set_slot(ship, None)
        db.flush()
        raise SlipdriveError(
            "The Slipdrive charge was cancelled -- you moved before it finished (no refund)"
        )

    deadline = _aware(datetime.fromisoformat(charge["deadline"]))
    if now < deadline:
        raise SlipdriveError(f"The Slipdrive is still charging until {deadline.isoformat()}")

    sectors, adjacency, sink_pks = _load_sector_graph(db)
    pk_to_sector_id = {s.id: s.sector_id for s in sectors}
    by_pk = {s.id: s for s in sectors}
    origin = next((s for s in sectors if s.sector_id == player.current_sector_id), None)
    if origin is None:
        raise SlipdriveError("Current sector has no charted coordinates")

    result = _bfs_nearest(
        origin.id, adjacency, lambda pk: pk not in sink_pks, lambda pk: pk_to_sector_id[pk]
    )
    if result is None:
        _set_slot(ship, None)
        db.flush()
        raise SlipdriveError(
            "no_recovery_target: the Slipdrive found no non-sink sector to route you to"
        )
    destination_pk, hops = result
    destination = by_pk[destination_pk]

    fuel_cost = _fuel_cost(hops)
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    fuel_held = int(contents.get(FUEL_COMMODITY_KEY, 0) or 0)
    if fuel_held < fuel_cost:
        raise SlipdriveError(
            f"insufficient_fuel: the Slipdrive needs {fuel_cost} fuel for a {hops}-hop escape; "
            f"you have {fuel_held}"
        )
    contents[FUEL_COMMODITY_KEY] = fuel_held - fuel_cost
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    # Teleport arrival -- mirrors quantum_service.jump()'s player-state sync
    # (sector, region, undock/unland flags, ship sector, presence broadcast)
    # WITHOUT any adjacency requirement: bypasses the warp graph, same as a
    # quantum jump, escaping a sink no real move could ever traverse.
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

    _set_slot(ship, None)

    db.flush()  # route owns the commit

    logger.info(
        "Player %s completed a Slipdrive escape: sector %s -> %s (%d hops, %d fuel)",
        player.id, old_sector_id, destination.sector_id, hops, fuel_cost,
    )

    return {
        "outcome": "slipdrive_escape",
        "destination_sector_id": destination.sector_id,
        "destination_name": destination.name,
        "hops": hops,
        "fuel_spent": fuel_cost,
        "fuel_remaining": contents.get(FUEL_COMMODITY_KEY, 0),
    }
