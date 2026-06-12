"""
Quantum Drive service — the Warp Jumper's signature ability.

Canon reference: sw2102-docs ADR-0030 (Quantum Jump multi-step commit) and
ADR-0031 (fuzzy long-range disclosure). Three-phase loop:

  Phase 1 — bearing-and-scan: cheap, repeatable recon (5 turns; +1 Quantum
            Shard for the Far band; Extended band gated behind Sensor L3).
            Returns FUZZY readings only — resonance band, texture word,
            binary echo — never exact counts. Results expire after 10
            REAL minutes (canon says real-minutes; deliberately NOT scaled
            through game_time). A 4h (canonical, scaled) scan cooldown runs
            on the WJ itself, decoupled from the jump cooldown.

  Phase 2 — commit: 1 Quantum Charge (ships.quantum_charges — the WJ's
            special-equipment slot, not cargo), 50 turns, 24h (canonical,
            scaled) jump cooldown. Irreversible.

  Phase 3 — resolve: candidates within 1.5 inter-sector spacings of the
            projected point, weighted by inverse distance; one 1.5x radius
            expansion; otherwise MISFIRE — arrive at the nearest sector to
            the bearing LINE short of the committed range, take a flat 5%
            max-hull hit (never below 1 hull; insurance does not cover it).

Bearing convention (shared with the client's Quantum Drive console):
  yaw_deg   0-360, measured counterclockwise from the +x axis in the
            galactic xy-plane (0 = +x, 90 = +y).
  pitch_deg -90..90, elevation toward +z.

INTER_SECTOR_SPACING ("one hop-unit") is interpreted as the median
nearest-neighbour Euclidean distance among a sample of the galaxy's
sectors, computed per call (cheap: bounded sample, coords-only query).
Range bands are expressed in these spacings.

Deliberately out of scope (parked, per section contract): anonymous
scan-detection events to defenders in the cone, and server-side scan
result persistence (the client holds results; the 10-minute expiry is
client-enforced and re-scanning is cheap).

All mutating entry points lock the player row (with_for_update) and
commit exactly once, mirroring movement_service.
"""
import logging
import math
import random
import uuid
from collections import Counter
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import scaled_deadline
from src.services.turn_service import spend_turns
from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType
from src.models.sector import Sector, SectorType, sector_warps
from src.models.station import Station

logger = logging.getLogger(__name__)


class QuantumError(Exception):
    """Raised for player-facing quantum drive failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 400."""


# --- Canonical constants (ADR-0030) ---

SCAN_TURN_COST = 5
JUMP_TURN_COST = 50
SCAN_COOLDOWN_HOURS = 4.0      # canonical, scaled via scaled_deadline
JUMP_COOLDOWN_HOURS = 24.0     # canonical, scaled via scaled_deadline
SCAN_RESULT_TTL_MINUTES = 10   # REAL minutes — canon says real-minutes, never scaled

CONE_HALF_ANGLE_DEG = 15.0
ACCURACY_RADIUS_SPACINGS = 1.5
RADIUS_EXPANSION_FACTOR = 1.5
MISFIRE_HULL_DAMAGE_PCT = 5.0  # flat % of max hull, insurance does not cover

MISREAD_BASE_PCT = 15
MISREAD_REDUCTION_PER_SENSOR_LEVEL = 5
EXTENDED_BAND_SENSOR_LEVEL = 3
FAR_BAND_SHARD_COST = 1
REFINE_MIN_STATION_CLASS = 3

# Range bands in inter-sector spacings: (min, max). Projection uses the
# band midpoint; the committed range (misfire ceiling) is the band max.
RANGE_BANDS: Dict[str, Tuple[float, float]] = {
    "near": (5.0, 6.0),
    "mid": (7.0, 8.0),
    "far": (9.0, 10.0),
    "extended": (12.0, 15.0),
}

# Fuzzy vocab orderings used for misread shifts (ADR-0031: a misread shifts
# the resonance band one level and swaps the texture for a near-relative).
RESONANCE_ORDER = ["silent", "faint", "steady", "bright"]
TEXTURE_ORDER = ["hollow", "mineral", "chromatic", "heavy", "hot", "turbulent"]

# Dominant-SectorType -> texture word mapping (documented per contract).
# STANDARD and every type not listed reads "turbulent"; an empty cone
# reads "hollow" (nothing out there).
TEXTURE_BY_SECTOR_TYPE = {
    SectorType.NEBULA: "chromatic",
    SectorType.ASTEROID_FIELD: "mineral",
    SectorType.VOID: "hollow",
    SectorType.BLACK_HOLE: "heavy",
    SectorType.STAR_CLUSTER: "hot",
}

_SPACING_SAMPLE_LIMIT = 200


# --- time helpers ---

def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _cooldown_active(until: Optional[datetime]) -> bool:
    until = _aware(until)
    return bool(until and until > _now())


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


# --- geometry helpers ---

def _bearing_unit_vector(yaw_deg: float, pitch_deg: float) -> Tuple[float, float, float]:
    """Unit direction vector for a yaw/pitch bearing (convention in module
    docstring: yaw CCW from +x in the xy-plane, pitch toward +z)."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    return (
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch),
    )


def _load_sector_points(db: Session) -> List[Any]:
    """Coords-only projection of every sector. Single-galaxy deployment:
    all sectors are same-galaxy, so 'same-galaxy only' (ADR-0030) is the
    full table; jumps may cross region boundaries within it."""
    return db.query(
        Sector.id,
        Sector.sector_id,
        Sector.name,
        Sector.x_coord,
        Sector.y_coord,
        Sector.z_coord,
        Sector.type,
        Sector.region_id,
    ).all()


def _inter_sector_spacing(points: List[Any]) -> float:
    """One hop-unit: the median nearest-neighbour Euclidean distance among
    a bounded, evenly-strided sample of the galaxy's sectors. Strided
    sampling (rather than random) keeps the value stable call-to-call so
    a scan and the jump it informs agree on band geometry."""
    if len(points) < 2:
        return 1.0
    if len(points) > _SPACING_SAMPLE_LIMIT:
        stride = len(points) // _SPACING_SAMPLE_LIMIT
        sample = points[::stride][:_SPACING_SAMPLE_LIMIT]
    else:
        sample = points
    nn_distances = []
    for p in sample:
        best = None
        for q in sample:
            if q is p:
                continue
            d = math.dist(
                (p.x_coord, p.y_coord, p.z_coord),
                (q.x_coord, q.y_coord, q.z_coord),
            )
            if d > 0 and (best is None or d < best):
                best = d
        if best is not None:
            nn_distances.append(best)
    if not nn_distances:
        return 1.0
    nn_distances.sort()
    median = nn_distances[len(nn_distances) // 2]
    return median if median > 1e-6 else 1.0


def _sectors_in_cone(
    points: List[Any],
    origin: Any,
    direction: Tuple[float, float, float],
    max_distance: float,
) -> List[Any]:
    """Sectors (excluding the origin) inside the 15-degree half-angle cone
    along `direction` out to `max_distance` (absolute units)."""
    cos_threshold = math.cos(math.radians(CONE_HALF_ANGLE_DEG))
    in_cone = []
    for p in points:
        if p.sector_id == origin.sector_id:
            continue
        dx = p.x_coord - origin.x_coord
        dy = p.y_coord - origin.y_coord
        dz = p.z_coord - origin.z_coord
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist <= 0 or dist > max_distance:
            continue
        cos_angle = (dx * direction[0] + dy * direction[1] + dz * direction[2]) / dist
        if cos_angle >= cos_threshold:
            in_cone.append(p)
    return in_cone


# --- shared validation helpers ---

def _lock_player(db: Session, player_id: uuid.UUID) -> Player:
    # populate_existing() forces a refresh from the locked row. with_for_update()
    # alone returns the identity-mapped instance already loaded by get_current_player
    # with PRE-LOCK state, so two concurrent refine/jump calls could both read the
    # same charges/turns and double-spend (gate-review finding; mirrors
    # construction_service.advance).
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not player:
        raise QuantumError("Player not found")
    return player


def _require_warp_jumper(db: Session, player: Player, refresh: bool = True) -> Ship:
    ship = player.current_ship
    if not ship or ship.is_destroyed:
        raise QuantumError("No active ship selected")
    if ship.type != ShipType.WARP_JUMPER:
        raise QuantumError("The quantum drive is exclusive to the Warp Jumper hull")
    if refresh:
        # Re-read the piloted ship under the player lock: charges and cooldowns
        # mutated by the caller must be read fresh, not from the pre-lock
        # identity map. Read-only paths (get_minimap) pass refresh=False —
        # only the hull check matters there and no lock is held.
        db.refresh(ship)
    return ship


def _sensor_level(ship: Ship) -> int:
    upgrades = ship.upgrades if isinstance(ship.upgrades, dict) else {}
    try:
        return int(upgrades.get("SENSOR", 0))
    except (TypeError, ValueError):
        return 0


def _validate_band(range_band: str) -> Tuple[float, float]:
    band = RANGE_BANDS.get(range_band)
    if band is None:
        raise QuantumError(
            f"Unknown range band: {range_band}. Valid bands: {list(RANGE_BANDS)}"
        )
    return band


def _origin_point(points: List[Any], sector_id: int) -> Any:
    origin = next((p for p in points if p.sector_id == sector_id), None)
    if origin is None:
        raise QuantumError("Current sector has no charted coordinates")
    return origin


# --- Phase 1: scan ---

def scan(
    db: Session,
    player_id: uuid.UUID,
    yaw_deg: float,
    pitch_deg: float,
    range_band: str,
) -> Dict[str, Any]:
    """Long-range quantum sweep (ADR-0030 Phase 1 / ADR-0031 fuzzy
    disclosure). Costs 5 turns (+1 Quantum Shard for the Far band), sets
    the 4h canonical scan cooldown, returns fuzzy cone readings."""
    player = _lock_player(db, player_id)
    ship = _require_warp_jumper(db, player)
    band_min, band_max = _validate_band(range_band)

    # Mirror jump()'s state guards BEFORE the cooldown check so a rejected
    # scan never consumes the cooldown (ADR-0040).
    if player.is_docked:
        raise QuantumError("You cannot run a quantum scan while docked — launch first")
    if player.is_landed:
        raise QuantumError("You cannot run a quantum scan on a planet surface — lift off first")
    if ship.status == ShipStatus.HARMONIZING:
        raise QuantumError("This Warp Jumper is anchored to a beacon and harmonizing — it cannot scan")

    if _cooldown_active(ship.quantum_scan_cooldown_until):
        raise QuantumError(
            f"Quantum sensors are recharging until "
            f"{_iso_or_none(ship.quantum_scan_cooldown_until)}"
        )

    sensor_level = _sensor_level(ship)
    if range_band == "extended" and sensor_level < EXTENDED_BAND_SENSOR_LEVEL:
        raise QuantumError(
            f"The extended range band requires a Sensor L{EXTENDED_BAND_SENSOR_LEVEL} upgrade"
        )

    shard_cost = FAR_BAND_SHARD_COST if range_band == "far" else 0
    if shard_cost and player.quantum_shards < shard_cost:
        raise QuantumError(
            f"Scanning the far band costs {shard_cost} Quantum Shard; you have "
            f"{player.quantum_shards}"
        )
    if player.turns < SCAN_TURN_COST:
        raise QuantumError(
            f"Not enough turns for a quantum scan. Need {SCAN_TURN_COST}, have {player.turns}"
        )

    points = _load_sector_points(db)
    origin = _origin_point(points, player.current_sector_id)
    if origin.type == SectorType.NEBULA:
        raise QuantumError(
            "Quantum field interference: the drive cannot lock a bearing inside a nebula"
        )
    spacing = _inter_sector_spacing(points)
    direction = _bearing_unit_vector(yaw_deg, pitch_deg)
    cone = _sectors_in_cone(points, origin, direction, band_max * spacing)

    # Resonance: fuzzy WARP-ACTIVITY band, never exact counts. Canon
    # (ADR-0030): resonance reads "warps' worth of activity" — bright 5+,
    # steady 3-4, faint 1-2, silent 0. Counting warp CONNECTIONS rooted in
    # the cone (not cone sectors: the minimap discloses positions, so a
    # sector count would be client-precomputable and worth nothing).
    warp_count = 0
    cone_pk_ids = [p.id for p in cone]
    if cone_pk_ids:
        warp_count = (
            db.query(func.count())
            .select_from(sector_warps)
            .filter(sector_warps.c.source_sector_id.in_(cone_pk_ids))
            .scalar()
            or 0
        )
    if warp_count >= 5:
        resonance = "bright"
    elif warp_count >= 3:
        resonance = "steady"
    elif warp_count >= 1:
        resonance = "faint"
    else:
        resonance = "silent"

    # Texture: dominant SectorType in the cone (TEXTURE_BY_SECTOR_TYPE map)
    if cone:
        dominant_type = Counter(p.type for p in cone).most_common(1)[0][0]
        texture = TEXTURE_BY_SECTOR_TYPE.get(dominant_type, "turbulent")
    else:
        texture = "hollow"

    # Echo: binary hostile-presence signal — any ship in the cone that
    # isn't the scanner's own (NPC ships have owner_id NULL)
    echo = "silent"
    cone_sector_ids = [p.sector_id for p in cone]
    if cone_sector_ids:
        other_ships = db.query(func.count(Ship.id)).filter(
            Ship.sector_id.in_(cone_sector_ids),
            Ship.is_destroyed == False,  # noqa: E712 — SQLAlchemy boolean comparison
            or_(Ship.owner_id != player.id, Ship.owner_id.is_(None)),
        ).scalar() or 0
        if other_ships > 0:
            echo = "faint motion"

    # Misread roll (ADR-0030: 15% minus 5 points per sensor level, floor 0;
    # a misread shifts resonance one band AND swaps texture for a neighbour)
    misread_pct = max(
        0, MISREAD_BASE_PCT - MISREAD_REDUCTION_PER_SENSOR_LEVEL * sensor_level
    )
    if misread_pct and random.random() < misread_pct / 100.0:
        r_idx = RESONANCE_ORDER.index(resonance)
        shift = random.choice([-1, 1])
        resonance = RESONANCE_ORDER[min(len(RESONANCE_ORDER) - 1, max(0, r_idx + shift))]
        t_idx = TEXTURE_ORDER.index(texture)
        shift = random.choice([-1, 1])
        texture = TEXTURE_ORDER[min(len(TEXTURE_ORDER) - 1, max(0, t_idx + shift))]

    # Charge the scan — single commit
    spend_turns(player, SCAN_TURN_COST)
    if shard_cost:
        player.quantum_shards -= shard_cost
    ship.quantum_scan_cooldown_until = scaled_deadline(SCAN_COOLDOWN_HOURS)
    db.commit()

    # Expiry is 10 REAL minutes (canon: real-minutes; deliberately unscaled)
    expires_at = _now() + timedelta(minutes=SCAN_RESULT_TTL_MINUTES)

    logger.info(
        "Player %s quantum-scanned band=%s yaw=%.1f pitch=%.1f -> %s/%s/%s",
        player.id, range_band, yaw_deg, pitch_deg, resonance, texture, echo,
    )

    return {
        "resonance": resonance,
        "texture": texture,
        "echo": echo,
        "expires_at": expires_at.isoformat(),
        "scan_cooldown_until": _iso_or_none(ship.quantum_scan_cooldown_until),
        "turns_remaining": player.turns,
    }


# --- Phases 2+3: commit and resolve ---

def jump(
    db: Session,
    player_id: uuid.UUID,
    yaw_deg: float,
    pitch_deg: float,
    range_band: str,
) -> Dict[str, Any]:
    """Commit-and-resolve (ADR-0030 Phases 2-3). Consumes 1 Quantum Charge
    + 50 turns and starts the 24h canonical jump cooldown REGARDLESS of
    outcome; resolves to a candidate sector or misfires onto the bearing
    line with 5% max-hull damage. Bypasses the warp graph entirely (no
    adjacency requirement); may cross region boundaries."""
    player = _lock_player(db, player_id)
    ship = _require_warp_jumper(db, player)
    band_min, band_max = _validate_band(range_band)

    # ADR-0040: no quantum jumps from a hangar — and none from a planet
    if player.is_docked:
        raise QuantumError("You cannot engage the quantum drive while docked — launch first")
    if player.is_landed:
        raise QuantumError("You cannot engage the quantum drive on a planet surface — lift off first")
    if ship.status == ShipStatus.HARMONIZING:
        raise QuantumError("This Warp Jumper is anchored to a beacon and harmonizing — it cannot jump")
    if _cooldown_active(ship.quantum_jump_cooldown_until):
        raise QuantumError(
            f"Quantum drive is in cooldown until "
            f"{_iso_or_none(ship.quantum_jump_cooldown_until)}"
        )
    # Player-wide 24h jump cooldown: the canon cooldown is per-pilot, not
    # per-hull. Swapping to another owned Warp Jumper must not reset it, so
    # reject if ANY owned ship still carries an active jump cooldown.
    fleet_jump_cd = db.query(func.max(Ship.quantum_jump_cooldown_until)).filter(
        Ship.owner_id == player.id,
        Ship.is_destroyed == False,  # noqa: E712 — SQLAlchemy boolean comparison
    ).scalar()
    if _cooldown_active(fleet_jump_cd):
        raise QuantumError(
            f"Quantum drive is in cooldown until {_iso_or_none(fleet_jump_cd)}"
        )
    # Same gate as the scan: committing blind to a band you cannot even
    # scan would sidestep the Sensor L3 requirement
    if range_band == "extended" and _sensor_level(ship) < EXTENDED_BAND_SENSOR_LEVEL:
        raise QuantumError(
            f"The extended range band requires a Sensor L{EXTENDED_BAND_SENSOR_LEVEL} upgrade"
        )
    if ship.quantum_charges < 1:
        raise QuantumError(
            "No Quantum Charge loaded. Refine one (1 Quantum Shard) at any "
            "Class-3+ station or SpaceDock"
        )
    if player.turns < JUMP_TURN_COST:
        raise QuantumError(
            f"Not enough turns for a quantum jump. Need {JUMP_TURN_COST}, have {player.turns}"
        )

    points = _load_sector_points(db)
    origin = _origin_point(points, player.current_sector_id)
    if origin.type == SectorType.NEBULA:
        raise QuantumError(
            "Quantum field interference: the drive cannot lock a bearing inside a nebula"
        )
    spacing = _inter_sector_spacing(points)
    direction = _bearing_unit_vector(yaw_deg, pitch_deg)

    # Phase 2 — commit. Irreversible: charge, turns and cooldown are
    # consumed no matter how the resolve lands (ADR-0030).
    ship.quantum_charges -= 1
    spend_turns(player, JUMP_TURN_COST)
    ship.quantum_jump_cooldown_until = scaled_deadline(JUMP_COOLDOWN_HOURS)

    # Phase 3 — resolve. Project the bearing to the band midpoint.
    committed_range = band_max * spacing
    projection_distance = ((band_min + band_max) / 2.0) * spacing
    target = (
        origin.x_coord + direction[0] * projection_distance,
        origin.y_coord + direction[1] * projection_distance,
        origin.z_coord + direction[2] * projection_distance,
    )

    candidates: List[Tuple[Any, float]] = []
    radius = ACCURACY_RADIUS_SPACINGS * spacing
    for _ in range(2):  # base radius, then ONE 1.5x expansion
        for p in points:
            if p.sector_id == origin.sector_id:
                continue
            d = math.dist((p.x_coord, p.y_coord, p.z_coord), target)
            if d <= radius:
                candidates.append((p, d))
        if candidates:
            break
        radius *= RADIUS_EXPANSION_FACTOR

    outcome = "jump"
    hull_damage_pct = 0.0

    if candidates:
        weights = [1.0 / max(d, 1e-6) for _, d in candidates]
        destination = random.choices([p for p, _ in candidates], weights=weights, k=1)[0]
    else:
        # MISFIRE: nearest existing sector to the bearing LINE, forward of
        # the origin and short of the committed range (ADR-0030 step 4)
        outcome = "misfire"
        hull_damage_pct = MISFIRE_HULL_DAMAGE_PCT
        best: Optional[Tuple[float, float, Any]] = None  # (perp, along, point)
        for p in points:
            if p.sector_id == origin.sector_id:
                continue
            dx = p.x_coord - origin.x_coord
            dy = p.y_coord - origin.y_coord
            dz = p.z_coord - origin.z_coord
            along = dx * direction[0] + dy * direction[1] + dz * direction[2]
            if along <= 1e-9 or along >= committed_range:
                continue
            dist_sq = dx * dx + dy * dy + dz * dz
            perp = math.sqrt(max(0.0, dist_sq - along * along))
            key = (perp, along)
            if best is None or key < (best[0], best[1]):
                best = (perp, along, p)
        if best is not None:
            destination = best[2]
        else:
            # Degenerate galaxy edge: nothing forward of the bearing within
            # range at all. The misfire collapses in place — the ship stays
            # put; the charge, turns, cooldown and hull damage still apply.
            destination = origin

        # Flat 5% max-hull damage, never below 1 hull, no insurance
        combat = ship.combat if isinstance(ship.combat, dict) else {}
        max_hull = combat.get("max_hull", combat.get("hull", 0)) or 0
        damage = max(1, int(round(max_hull * MISFIRE_HULL_DAMAGE_PCT / 100.0)))
        combat["hull"] = max(1, combat.get("hull", max_hull) - damage)
        ship.combat = combat
        flag_modified(ship, "combat")

    distance_jumped = round(
        math.dist(
            (origin.x_coord, origin.y_coord, origin.z_coord),
            (destination.x_coord, destination.y_coord, destination.z_coord),
        ) / spacing,
        1,
    )

    # Execute the arrival — mirrors movement_service._execute_movement's
    # player-state sync (sector, region, undock flags, ship sector) WITHOUT
    # the adjacency requirement: the quantum jump bypasses the warp graph.
    old_sector_id = player.current_sector_id
    if destination.sector_id != old_sector_id:
        player.current_sector_id = destination.sector_id
        player.current_region_id = destination.region_id
        player.is_docked = False
        player.is_landed = False
        player.current_port_id = None
        player.current_planet_id = None
        ship.sector_id = destination.sector_id

        # Reuse the canonical players_present bookkeeping rather than
        # duplicating it (private by convention, single source of truth)
        from src.services.movement_service import MovementService
        MovementService(db)._update_player_presence(
            player, old_sector_id, destination.sector_id
        )

    db.commit()

    logger.info(
        "Player %s quantum %s: sector %s -> %s (band=%s, %.1f spacings)",
        player.id, outcome, old_sector_id, destination.sector_id,
        range_band, distance_jumped,
    )

    return {
        "outcome": outcome,
        "destination_sector_id": destination.sector_id,
        "destination_name": destination.name,
        "distance_jumped": distance_jumped,
        "hull_damage_pct": hull_damage_pct,
        "jump_cooldown_until": _iso_or_none(ship.quantum_jump_cooldown_until),
        "turns_remaining": player.turns,
    }


# --- refining ---

def refine_charge(db: Session, player_id: uuid.UUID) -> Dict[str, Any]:
    """Refine 1 Quantum Shard into 1 Quantum Charge on the piloted Warp
    Jumper. Venue rule (ADR-0030 / ADR-0009): docked at any Class-3+
    station or SpaceDock."""
    player = _lock_player(db, player_id)
    ship = _require_warp_jumper(db, player)

    if not player.is_docked or not player.current_port_id:
        raise QuantumError("You must be docked at a station to refine a Quantum Charge")
    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise QuantumError("Docked station not found")
    if not (station.is_spacedock or station.station_class.value >= REFINE_MIN_STATION_CLASS):
        raise QuantumError(
            f"Quantum Charge refining requires a Class-{REFINE_MIN_STATION_CLASS}+ "
            f"station or SpaceDock; {station.name} is Class {station.station_class.value}"
        )
    if player.quantum_shards < 1:
        raise QuantumError(
            f"Refining a Quantum Charge costs 1 Quantum Shard; you have {player.quantum_shards}"
        )

    player.quantum_shards -= 1
    ship.quantum_charges += 1
    db.commit()

    logger.info(
        "Player %s refined a Quantum Charge on %s at station %s (charges=%d, shards=%d)",
        player.id, ship.name, station.id, ship.quantum_charges, player.quantum_shards,
    )

    return {
        "quantum_charges": ship.quantum_charges,
        "quantum_shards": player.quantum_shards,
    }


# --- minimap (astrogation chart) ---

# ADR-0030 Phase 1: "shown a 3D minimap of sectors within roughly 25
# hop-units Euclidean distance."
MINIMAP_RANGE_SPACINGS = 25.0
# Payload cap: keep the chart to the ~400 NEAREST sectors by Euclidean
# distance. A dense galaxy could put thousands of sectors inside the
# 25-spacing sphere; the client plots a ~16-spacing viewport anyway, so
# the nearest 400 always cover everything it can draw.
MINIMAP_SECTOR_CAP = 400


def get_minimap(db: Session, player: Player) -> Dict[str, Any]:
    """Astrogation chart for the Quantum Drive console (ADR-0030 Phase 1).

    READ-ONLY and always available to a Warp Jumper pilot: no turn cost,
    no cooldown, allowed while docked or landed — the astrogation plot is
    just the chart, not a sensor sweep.

    DISCLOSURE LIMIT (ADR-0031): relative POSITIONS ONLY ("the specific
    sector ID is not disclosed" — bearing-and-band commits, not identity).
    No sector ids, no type, no name, no activity, no player presence. The
    fuzzy echo scan is the only telescope; the minimap is the chart it is
    read against.

    Returns coordinates RELATIVE to the origin sector (dx/dy/dz in the
    galaxy's absolute coordinate units) plus the same inter-sector spacing
    the scan/jump code paths compute, so viewport band geometry and server
    band geometry always agree. ``complete_radius_spacings`` reports how
    far (in spacings) the returned chart is COMPLETE: 25.0 when nothing was
    truncated, otherwise the distance of the furthest returned sector — the
    client dims coverage beyond it so a truncated chart never reads as
    empty space.
    """
    # Reuse the hull requirement helper (no player lock: nothing mutates;
    # only the Warp-Jumper check matters, the returned ship is unused —
    # refresh=False skips the pointless db.refresh on this read-only path).
    _require_warp_jumper(db, player, refresh=False)

    points = _load_sector_points(db)
    origin = _origin_point(points, player.current_sector_id)
    spacing = _inter_sector_spacing(points)
    max_distance = MINIMAP_RANGE_SPACINGS * spacing

    nearby: List[Tuple[float, float, float, float]] = []
    for p in points:
        if p.sector_id == origin.sector_id:
            continue
        dx = p.x_coord - origin.x_coord
        dy = p.y_coord - origin.y_coord
        dz = p.z_coord - origin.z_coord
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist <= max_distance:
            nearby.append((dist, dx, dy, dz))

    nearby.sort(key=lambda r: r[0])
    truncated = len(nearby) > MINIMAP_SECTOR_CAP
    nearby = nearby[:MINIMAP_SECTOR_CAP]

    # Cap honesty: when truncated, the chart is only complete out to the
    # furthest sector we actually returned; report that radius (in
    # spacings) so the client can dim the unreliable annulus beyond it.
    if truncated and nearby:
        complete_radius_spacings = round(nearby[-1][0] / spacing, 2)
    else:
        complete_radius_spacings = MINIMAP_RANGE_SPACINGS

    return {
        "origin_sector_id": origin.sector_id,
        "spacing": spacing,
        "complete_radius_spacings": complete_radius_spacings,
        # ADR-0031: positions only — deliberately NO sector ids
        "sectors": [
            {"dx": dx, "dy": dy, "dz": dz}
            for _, dx, dy, dz in nearby
        ],
    }


# --- status ---

def get_status(db: Session, player: Player) -> Dict[str, Any]:
    """Read-only quantum drive status for the client console. Expired
    cooldowns serialize as null so the client never renders a stale timer."""
    ship = player.current_ship
    is_warp_jumper = bool(
        ship and not ship.is_destroyed and ship.type == ShipType.WARP_JUMPER
    )

    # Jump cooldown is per-pilot, not per-hull: surface the player-wide max
    # across all owned ships so swapping hulls can't hide an active cooldown
    # (mirrors jump()'s fleet-wide gate).
    jump_cd = (
        db.query(func.max(Ship.quantum_jump_cooldown_until))
        .filter(
            Ship.owner_id == player.id,
            Ship.is_destroyed == False,  # noqa: E712 — SQLAlchemy boolean comparison
        )
        .scalar()
        if is_warp_jumper
        else None
    )
    scan_cd = ship.quantum_scan_cooldown_until if is_warp_jumper else None
    charges = ship.quantum_charges if is_warp_jumper else 0
    sensor_level = _sensor_level(ship) if is_warp_jumper else 0

    can_jump = (
        is_warp_jumper
        and not player.is_docked
        and not player.is_landed
        and ship.status != ShipStatus.HARMONIZING
        and not _cooldown_active(jump_cd)
        and charges >= 1
        and player.turns >= JUMP_TURN_COST
    )

    return {
        "quantum_shards": player.quantum_shards,
        "quantum_crystals": player.quantum_crystals,
        "quantum_charges": charges,
        "jump_cooldown_until": _iso_or_none(jump_cd) if _cooldown_active(jump_cd) else None,
        "scan_cooldown_until": _iso_or_none(scan_cd) if _cooldown_active(scan_cd) else None,
        "can_jump": can_jump,
        "is_warp_jumper": is_warp_jumper,
        "sensor_level": sensor_level,
    }
