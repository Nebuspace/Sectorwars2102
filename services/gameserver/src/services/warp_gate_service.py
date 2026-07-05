"""
Warp-gate construction service (ADR-0029 + ADR-0078 + FEATURES/galaxy/
warp-gates.md).

Three-phase ritual, all lazily settled — there is no background worker:

  deploy_beacon  — Phase 1: validations (free), then 50 turns + 10,000 cr +
                   1 Quantum Crystal. Beacon enters the 48h invulnerability/
                   expiry window (ADR-0011); a GateConstructionSite (phase 1)
                   opens alongside it to accumulate the 1,000 ore + 500
                   equipment structure draw over multiple stage-materials runs
                   (ADR-0078 — a Warp Jumper's 200-unit hold can't fit either
                   phase's total in one trip).
  stage_materials — deposit ore/equipment (from the depositing ship's cargo)
                   or Lumen Crystals (from the depositing PLAYER's
                   Player.lumen_crystals wallet) into a site. Any ship present
                   in the beacon's sector may deposit, not only the beacon's
                   owner — team hauling is the point.
  advance_construction — once a site's totals are fully staged, spends
                   CONSTRUCTION_TURN_COST (5) turns to start its
                   PHASE_CURE_HOURS (24) cure. Lazily flips CURING -> READY
                   on any subsequent touch of the site (mirrors advance_gate's
                   lazy-on-read model) — reaching READY on the Phase-1 site
                   auto-opens the Phase-3 site for the same beacon.
  anchor_focus   — Phase 3 Step A: 100 turns + 10,000 cr, drawn against the
                   Phase-3 site's staged + cured 1,000 ore + 500 equipment +
                   30 Lumen Crystals (never the Warp Jumper's hold). Warp
                   Jumper enters HARMONIZING for one canonical hour; the
                   WarpGate row and the FORMING WarpTunnel row are created NOW.
  advance_gate   — Phase 3 Step B (lazy, called from every read/list/traversal
                   path): past the timer the Warp Jumper hull is consumed
                   (no insurance, no Cargo Wreck, full cargo to the escape pod
                   at the DESTINATION), tunnel + gate flip ACTIVE, beacon
                   MATCHED with invulnerability cleared.
  cancel         — beacon: Phase 1 materials sunk (canon), INCLUDING whatever
                   is staged in the Phase-1 site. A staged-but-unconsumed
                   Phase-3 site (opened once Phase 1 cured) is NOT covered by
                   that rule — ADR-0029 is silent on it under the new staging
                   model — so ore/equipment return to the cancelling player's
                   ship hold up to its remaining capacity (excess forfeited;
                   no warp-gate salvage-wreck mechanic exists to spawn
                   instead) and staged Lumen Crystals always refund in full to
                   Player.lumen_crystals (a wallet ledger, no capacity
                   concept) — builder-proposed disposition, flagged to
                   DECISIONS. Harmonizing gate: full Phase 3 refund (turns/
                   credits to the player; ore/equipment/Lumen back into the
                   Phase-3 site per warp-gates.md's own Phase 3 failure-mode
                   wording, ready to redraw without re-ferrying or re-curing),
                   ship exits HARMONIZING intact, tunnel row deleted. The
                   Phase 1 Crystal never refunds.

All canonical durations go through src/core/game_time.scaled_deadline so
GAME_TIME_SCALE compresses them uniformly on dev.

Lock-ordering contract (mirrors construction_service's station-before-player
rule): the BEACON/GATE row is locked first, the PLAYER row second. No function
here commits — the calling route owns the transaction boundary.

Interpretations where canon leaves room (documented per NEON rules):
  * "nexus-protected" sectors: Sector has no dedicated is_nexus_protected
    column — we read the `nexus_protected` entry of Sector.special_features,
    the same ARRAY that carries `no_warp`.
  * Central Nexus region: identified by Region.region_type == "central_nexus"
    (RegionType.CENTRAL_NEXUS) — regions carry no other nexus marker.
  * Region ownership for the gate-cap formula: Region.owner_id is a User FK,
    so a player "owns a region" when any Region row has
    owner_id == player.user_id.
  * Minimum gate length: accepted when the straight-line (Euclidean) distance
    between the sector grid coordinates is >= 50, OR when a bounded BFS over
    the natural sector_warps network cannot reach the destination within 49
    hops (i.e. the natural-network path-distance is >= 50 or the endpoints
    are unconnected, per warp-gates.md "Placement constraints").
  * construction_cost snapshot: Warp Jumper spec base_cost (1,000,000 — the
    seeder and construction_service now seed the canon 1M figure) + the Phase 1
    and Phase 3 credit costs (2 x 10,000) = 1,020,000.
"""
import logging
import math
import uuid
from collections import defaultdict, deque
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import scaled_deadline
from src.models.gate_construction_site import GateConstructionSite, GateConstructionSiteStatus
from src.models.player import Player
from src.models.region import Region, RegionType
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType
from src.models.team_member import TeamMember
from src.models.warp_gate import (
    WarpGate,
    WarpGateBeacon,
    WarpGateBeaconStatus,
    WarpGateStatus,
)
from src.models.warp_tunnel import (
    WarpTunnel,
    WarpTunnelStatus,
    WarpTunnelType,
)
from src.services.ship_service import ShipService
from src.services.turn_service import spend_turns, refund_turns

logger = logging.getLogger(__name__)


# --- Canonical costs and limits (FEATURES/galaxy/warp-gates.md) -------------

PHASE1_TURNS = 50
PHASE1_CREDITS = 10_000
PHASE1_ORE = 1_000
PHASE1_EQUIPMENT = 500
PHASE1_QUANTUM_CRYSTALS = 1

PHASE3_TURNS = 100
PHASE3_CREDITS = 10_000
PHASE3_ORE = 1_000
PHASE3_EQUIPMENT = 500
PHASE3_LUMEN_CRYSTALS = 30

BEACON_WINDOW_HOURS = 48      # ADR-0011 invulnerability == expiry window
HARMONIZATION_HOURS = 1       # ADR-0029 Phase 3 wait
MIN_GATE_LENGTH = 50          # sectors
MAX_INCOMING_ACTIVE_GATES = 5  # destination anti-spam cap

# --- ADR-0078 staged construction (warp-gates.md "Material staging") -------
# A phase's bulk ORE/EQUIPMENT/LUMEN_CRYSTALS accumulate in a
# GateConstructionSite across partial deposits (<=200/run for a Warp Jumper,
# more for teammates' bigger haulers) instead of being demanded in the
# Warp Jumper's hold in one payload. Once a site holds a full phase's
# materials, advance_construction spends CONSTRUCTION_TURN_COST turns to
# start the site's PHASE_CURE_HOURS cure; the phase can only be drawn once
# both the totals and the cure are satisfied.
CONSTRUCTION_TURN_COST = 5    # advance-construction, per call (ADR-0078)
PHASE_CURE_HOURS = 24         # canonical hours per phase (ADR-0078)
# NO-CANON (flagged to DECISIONS): canon prices advance-construction's 5
# turns but is silent on a turn cost for the stage-materials deposit call
# itself -- proposing 0 (ferrying materials is the trip-time cost; the
# construction turns are spent only at advance-construction).
STAGE_MATERIALS_TURN_COST = 0

NO_WARP_FEATURE = "no_warp"
NEXUS_PROTECTED_FEATURE = "nexus_protected"

# --- Access modes (WO-DBB-WG1, warp-gates.md "Access control") --------------
# The owner picks one mode; whitelist + allies layer on top. The mode and its
# layers live in WarpTunnel.access_requirements JSONB (canon: "full access modes
# live in access_requirements"). The traversable connection IS the WarpTunnel
# row, so enforcement reads it there.

ACCESS_MODE_PUBLIC = "PUBLIC"        # anyone (default)
ACCESS_MODE_TEAM_ONLY = "TEAM_ONLY"  # owner + members of owner's team
ACCESS_MODE_PRIVATE = "PRIVATE"      # owner only
ACCESS_MODE_WHITELIST = "WHITELIST"  # owner + specific player UUIDs
ACCESS_MODE_ALLIANCE = "ALLIANCE"    # owner's team + allied teams

ACCESS_MODES = frozenset({
    ACCESS_MODE_PUBLIC,
    ACCESS_MODE_TEAM_ONLY,
    ACCESS_MODE_PRIVATE,
    ACCESS_MODE_WHITELIST,
    ACCESS_MODE_ALLIANCE,
})

# Mode -> is_public coarse flag (warp-gates.md: is_public is a "coarse flag",
# the authoritative mode lives in access_requirements). Only PUBLIC sets the
# coarse public flag; every gated mode clears it.
_PUBLIC_MODES = frozenset({ACCESS_MODE_PUBLIC})

# No-canon cap on how many entries an owner may set in the whitelist / allies
# lists in a single permissions call — a conservative DoS guard so a malicious
# owner cannot bloat the JSONB. NOT a documented game number.
MAX_ACCESS_LIST_ENTRIES = 200  # NO-CANON


class WarpGateError(Exception):
    """Carries an HTTP status + human detail string up to the route layer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Small helpers ----------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _lock_player(db: Session, player_id) -> Player:
    # populate_existing() forces a refresh from the locked row — with_for_update()
    # alone returns the identity-mapped instance with stale attributes, so two
    # concurrent settlements could read a stale balance and double-apply
    # (construction_service.py:407 precedent; FIX 3).
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if player is None:
        raise WarpGateError(404, "Player not found")
    return player


def _sector_by_number(db: Session, sector_number: int) -> Optional[Sector]:
    return db.query(Sector).filter(Sector.sector_id == sector_number).first()


def _require_warp_jumper(db: Session, player: Player, action: str) -> Ship:
    """The player must be piloting a Warp Jumper, in open space."""
    if player.is_docked or player.is_landed:
        raise WarpGateError(
            400, f"You must be in open space (not docked or landed) to {action}"
        )
    if not player.current_ship_id:
        raise WarpGateError(400, "No active ship selected")
    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id,
        Ship.owner_id == player.id,
    ).first()
    if ship is None:
        raise WarpGateError(404, "No active ship found")
    if ship.is_destroyed:
        raise WarpGateError(400, "That hull is destroyed")
    if ship.status == ShipStatus.HARMONIZING:
        raise WarpGateError(
            400, "That hull is already harmonizing into a gate focus"
        )
    if ship.type != ShipType.WARP_JUMPER:
        raise WarpGateError(
            400, f"Only a Warp Jumper can {action} — you are piloting a "
                 f"{ship.type.value.replace('_', ' ').title()}"
        )
    return ship


def _cargo_contents(ship: Ship) -> Dict[str, Any]:
    cargo = ship.cargo or {"capacity": 0, "used": 0, "contents": {}}
    if "contents" not in cargo or cargo["contents"] is None:
        cargo["contents"] = {}
    return cargo


def _charge_cargo(ship: Ship, requirements: Dict[str, int]) -> None:
    """Deduct already-validated quantities from the active ship's cargo."""
    cargo = _cargo_contents(ship)
    contents = cargo["contents"]
    for key, qty in requirements.items():
        remaining = int(contents.get(key, 0) or 0) - qty
        if remaining > 0:
            contents[key] = remaining
        else:
            contents.pop(key, None)
    cargo["used"] = sum(
        int(q) for q in contents.values() if isinstance(q, (int, float))
    )
    ship.cargo = cargo
    flag_modified(ship, "cargo")


def _refund_cargo(ship: Ship, amounts: Dict[str, int]) -> None:
    """Return materials to the ship's cargo. The ship was frozen in
    HARMONIZING since the deduction, so the freed space is still there —
    no clamping is needed (and canon's full Phase 3 refund outranks it)."""
    cargo = _cargo_contents(ship)
    contents = cargo["contents"]
    for key, qty in amounts.items():
        contents[key] = int(contents.get(key, 0) or 0) + qty
    cargo["used"] = sum(
        int(q) for q in contents.values() if isinstance(q, (int, float))
    )
    ship.cargo = cargo
    flag_modified(ship, "cargo")


def _refund_cargo_up_to_capacity(ship: Ship, amounts: Dict[str, int]) -> Dict[str, int]:
    """Return as much of `amounts` to the ship's cargo as fits under its
    remaining capacity (unlike _refund_cargo, which assumes the space is
    already free). Used for the ADR-0078 beacon-cancel staged-material
    disposition, where the depositing ship never had this cargo deducted in
    the first place — capacity is NOT guaranteed to be free. Returns the
    subset actually applied; any remainder is the caller's to account for
    (forfeited, per the beacon-cancel disposition)."""
    cargo = _cargo_contents(ship)
    capacity = int(cargo.get("capacity", 0) or 0)
    used = int(cargo.get("used", 0) or 0)
    room = max(0, capacity - used)
    applied: Dict[str, int] = {}
    for key, qty in amounts.items():
        if qty <= 0 or room <= 0:
            continue
        take = min(qty, room)
        applied[key] = take
        room -= take
    if applied:
        _refund_cargo(ship, applied)
    return applied


# --- Placement validation (free — runs before any charge) -------------------

def _check_special_features(sector: Sector, role: str) -> None:
    features = sector.special_features or []
    if NO_WARP_FEATURE in features:
        raise WarpGateError(
            400,
            f"{role} sector {sector.sector_id} ({sector.name}) is a no-warp "
            "zone — warp gate endpoints cannot be placed there",
        )
    if NEXUS_PROTECTED_FEATURE in features:
        raise WarpGateError(
            400,
            f"{role} sector {sector.sector_id} ({sector.name}) is protected "
            "by the Nexus Sentinel Corps — warp gate construction is "
            "prohibited there",
        )


def _check_same_region(db: Session, player: Player, sector: Sector, role: str) -> None:
    """Both endpoints must lie in the player's current region OR in the
    Central Nexus region (identified by region_type == central_nexus)."""
    if sector.region_id == player.current_region_id:
        return
    region = db.query(Region).filter(Region.id == sector.region_id).first() if sector.region_id else None
    if region is not None and region.region_type == RegionType.CENTRAL_NEXUS:
        return
    raise WarpGateError(
        400,
        f"{role} sector {sector.sector_id} ({sector.name}) is outside your "
        "current region — gates may only connect sectors in your region or "
        "the Central Nexus",
    )


def _check_incoming_gate_cap(
    db: Session,
    destination_sector_number: int,
    include_harmonizing: bool = False,
) -> None:
    """Count incoming gates toward the destination anti-spam cap.

    At deploy/completion time we count ACTIVE gates only (the live cap).
    At anchor time we additionally count HARMONIZING gates targeting the
    destination so N concurrent anchors cannot all complete past the cap —
    the completion-time re-validation (FIX 2a) is the backstop, this is the
    front-stop (FIX 4)."""
    statuses = [WarpGateStatus.ACTIVE]
    if include_harmonizing:
        statuses.append(WarpGateStatus.HARMONIZING)
    incoming = (
        db.query(WarpGate)
        .join(WarpGateBeacon, WarpGate.beacon_id == WarpGateBeacon.id)
        .filter(
            WarpGateBeacon.destination_sector_id == destination_sector_number,
            WarpGate.status.in_(statuses),
        )
        .count()
    )
    if incoming >= MAX_INCOMING_ACTIVE_GATES:
        raise WarpGateError(
            400,
            f"Sector {destination_sector_number} already has "
            f"{MAX_INCOMING_ACTIVE_GATES} incoming active warp gates — "
            "further incoming gates are rejected",
        )


def _check_min_gate_length(db: Session, source: Sector, destination: Sector) -> None:
    """Reject gates shorter than 50 sectors (warp-gates.md).

    Accept immediately when the straight-line grid distance is >= 50;
    otherwise run a bounded BFS over the natural sector_warps network — if the
    destination is reachable within 49 hops the gate is too short; if BFS
    exhausts without reaching it the endpoints are >= 50 hops apart or
    unconnected, both acceptable."""
    dx = (source.x_coord or 0) - (destination.x_coord or 0)
    dy = (source.y_coord or 0) - (destination.y_coord or 0)
    dz = (source.z_coord or 0) - (destination.z_coord or 0)
    if math.sqrt(dx * dx + dy * dy + dz * dz) >= MIN_GATE_LENGTH:
        return

    rows = db.execute(sector_warps.select()).fetchall()
    adjacency = defaultdict(set)
    for row in rows:
        adjacency[row.source_sector_id].add(row.destination_sector_id)
        if row.is_bidirectional:
            adjacency[row.destination_sector_id].add(row.source_sector_id)

    visited = {source.id}
    queue = deque([(source.id, 0)])
    while queue:
        node, depth = queue.popleft()
        if depth >= MIN_GATE_LENGTH - 1:
            continue  # anything found beyond this depth is >= 50 hops away
        for neighbor in adjacency.get(node, ()):
            if neighbor in visited:
                continue
            if neighbor == destination.id:
                raise WarpGateError(
                    400,
                    f"Gate too short — sector {destination.sector_id} is only "
                    f"{depth + 1} hops away; the minimum gate length is "
                    f"{MIN_GATE_LENGTH} sectors",
                )
            visited.add(neighbor)
            queue.append((neighbor, depth + 1))


def max_gates_for_player(db: Session, player: Player) -> int:
    """ADR-0010 cap: 1 + floor(active_team_size / 4) + 3 if region owner.

    active_team_size counts TeamMember rows of the player's current team
    (0 when teamless). Region ownership reads Region.owner_id (a User FK)
    against player.user_id — see module docstring."""
    team_size = 0
    if player.team_id:
        team_size = (
            db.query(TeamMember).filter(TeamMember.team_id == player.team_id).count()
        )
    owns_region = (
        db.query(Region).filter(Region.owner_id == player.user_id).first() is not None
    )
    return 1 + (team_size // 4) + (3 if owns_region else 0)


def _check_gate_cap(db: Session, player: Player, include_harmonizing: bool = False) -> None:
    """ADR-0010 ownership cap.

    At deploy time we count ACTIVE gates only. At anchor time we count
    ACTIVE + HARMONIZING — parallel in-progress projects each reserve a
    slot, so N concurrent anchors cannot all complete past the cap
    (FIX 4)."""
    cap = max_gates_for_player(db, player)
    statuses = [WarpGateStatus.ACTIVE]
    if include_harmonizing:
        statuses.append(WarpGateStatus.HARMONIZING)
    active = (
        db.query(WarpGate)
        .filter(WarpGate.player_id == player.id, WarpGate.status.in_(statuses))
        .count()
    )
    if active >= cap:
        raise WarpGateError(
            400,
            f"You already own {active} active warp gate(s); your current "
            f"limit is {cap}. Grow your team or acquire a region to raise it",
        )


# --- Phase 1: deploy beacon -------------------------------------------------

def deploy_beacon(db: Session, player: Player, destination_sector_number: int) -> Dict[str, Any]:
    """Phase 1 — validations cost nothing; on pass, charge and create the
    beacon with its 48h invulnerability/expiry window."""
    now = datetime.now(UTC)
    player = _lock_player(db, player.id)
    # Materials no longer come out of this hull's cargo (ADR-0078 staging
    # below) — the call is still required for its Warp-Jumper-in-open-space
    # validation, the return value just isn't needed anymore.
    _require_warp_jumper(db, player, "deploy a warp gate beacon")

    source = _sector_by_number(db, player.current_sector_id)
    if source is None:
        raise WarpGateError(404, "Your current sector could not be found")
    destination = _sector_by_number(db, destination_sector_number)
    if destination is None:
        raise WarpGateError(404, f"Destination sector {destination_sector_number} not found")

    # Validation failures cost NOTHING (warp-gates.md) — run every check
    # before any deduction.
    if source.sector_id == destination.sector_id:
        raise WarpGateError(400, "A warp gate cannot loop back to its own sector")

    # Sentinel-protected sectors (police-forces.md): Phase 1 deployment
    # touching a protected Nexus sector is rejected at the API layer AND
    # the Sentinel response fires anyway — the intercept is what makes
    # the rejection load-bearing. The engagement row is committed before
    # the rejection raises so it survives the error response.
    for endpoint in (source, destination):
        if getattr(endpoint, "is_nexus_protected", False):
            try:
                from src.services import npc_engagement_service
                npc_engagement_service.route_engagement(
                    db, player, "protected_sector_breach", endpoint,
                    include_captain=True,
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Sentinel intercept dispatch failed (non-fatal)")
            raise WarpGateError(
                403,
                "ERR_NEXUS_PROTECTED_SECTOR: warp-gate construction is "
                "prohibited in Sentinel-protected sectors — a Sentinel "
                "squad has been dispatched",
            )
    _check_special_features(source, "Source")
    _check_special_features(destination, "Destination")
    _check_same_region(db, player, source, "Source")
    _check_same_region(db, player, destination, "Destination")
    _check_min_gate_length(db, source, destination)
    _check_incoming_gate_cap(db, destination.sector_id)
    _check_gate_cap(db, player)

    if player.turns < PHASE1_TURNS:
        raise WarpGateError(
            400, f"Deploying a beacon costs {PHASE1_TURNS} turns; you have {player.turns}"
        )
    if player.credits < PHASE1_CREDITS:
        raise WarpGateError(
            400,
            f"Deploying a beacon costs {PHASE1_CREDITS:,} credits; "
            f"you have {player.credits:,}",
        )
    crystals = getattr(player, "quantum_crystals", 0) or 0
    if crystals < PHASE1_QUANTUM_CRYSTALS:
        raise WarpGateError(
            400,
            "Deploying a beacon consumes 1 Quantum Crystal — assemble one "
            "from 5 Quantum Shards at a Class 3+ station or SpaceDock",
        )
    # ADR-0078: the 1,000 ore + 500 equipment structure draw is NOT demanded
    # from the ship's hold here — it stages into the construction site opened
    # below, across as many stage-materials runs as it takes (a Warp Jumper's
    # 200-unit hold can't fit it in one trip).

    # All checks passed — charge atomically.
    spend_turns(player, PHASE1_TURNS)
    player.credits -= PHASE1_CREDITS
    player.quantum_crystals = crystals - PHASE1_QUANTUM_CRYSTALS

    beacon = WarpGateBeacon(
        player_id=player.id,
        source_sector_id=source.sector_id,
        destination_sector_id=destination.sector_id,
        status=WarpGateBeaconStatus.DEPLOYED,
        invulnerable_until=scaled_deadline(BEACON_WINDOW_HOURS, now),
    )
    db.add(beacon)
    db.flush()

    site = GateConstructionSite(
        beacon_id=beacon.id,
        phase=1,
        required_ore=PHASE1_ORE,
        required_equipment=PHASE1_EQUIPMENT,
        required_lumen=0,
        status=GateConstructionSiteStatus.STAGING,
    )
    db.add(site)
    db.flush()

    logger.info(
        "Player %s deployed warp gate beacon %s (%s -> %s); construction site "
        "%s opened for Phase 1 staging",
        player.id, beacon.id, source.sector_id, destination.sector_id, site.id,
    )
    return {
        "beacon": beacon,
        "site_id": str(site.id),
        "costs_charged": {
            "turns": PHASE1_TURNS,
            "credits": PHASE1_CREDITS,
            "quantum_crystals": PHASE1_QUANTUM_CRYSTALS,
        },
    }


# --- ADR-0078 staged construction: stage-materials / advance-construction ---

def _lazy_advance_site_cure(
    db: Session, site: GateConstructionSite, now: Optional[datetime] = None
) -> None:
    """Lazy, read-time cure completion (ADR-0078 — "lazy advance-on-read,
    mirroring terraforming's tick model", no background worker). Flips a
    CURING site whose PHASE_CURE_HOURS scaled cure has elapsed to READY.
    Reaching READY on a Phase-1 site auto-opens the Phase-3 site for the same
    beacon (canon: "before the next phase opens") — idempotent, guarded
    against a duplicate under concurrent lazy-advance calls."""
    if site.status != GateConstructionSiteStatus.CURING:
        return
    now = now or datetime.now(UTC)
    if site.cure_completes_at is None or _aware(now) < _aware(site.cure_completes_at):
        return
    site.status = GateConstructionSiteStatus.READY
    db.flush()
    if site.phase != 1:
        return
    existing = (
        db.query(GateConstructionSite)
        .filter(
            GateConstructionSite.beacon_id == site.beacon_id,
            GateConstructionSite.phase == 3,
        )
        .first()
    )
    if existing is not None:
        return
    phase3_site = GateConstructionSite(
        beacon_id=site.beacon_id,
        phase=3,
        required_ore=PHASE3_ORE,
        required_equipment=PHASE3_EQUIPMENT,
        required_lumen=PHASE3_LUMEN_CRYSTALS,
        status=GateConstructionSiteStatus.STAGING,
    )
    db.add(phase3_site)
    db.flush()
    logger.info(
        "Phase 1 construction site %s cured — Phase 3 site %s opened for beacon %s",
        site.id, phase3_site.id, site.beacon_id,
    )


def _resolve_site(db: Session, site_id: str, *, lock: bool) -> GateConstructionSite:
    try:
        site_uuid = uuid.UUID(str(site_id))
    except (ValueError, AttributeError, TypeError):
        raise WarpGateError(404, "Construction site not found")
    query = db.query(GateConstructionSite).filter(GateConstructionSite.id == site_uuid)
    if lock:
        query = query.with_for_update()
    site = query.first()
    if site is None:
        raise WarpGateError(404, "Construction site not found")
    return site


def stage_materials(
    db: Session, player: Player, site_id: str, amounts: Dict[str, Optional[int]]
) -> Dict[str, Any]:
    """ADR-0078 — deposit ore / equipment / Lumen Crystals into a
    gate_construction_site. Any ship present in the site's sector may deposit
    (warp-gates.md "Any ship may deposit" — team hauling is the point); ore
    and equipment draw from the depositing ship's cargo, Lumen Crystals draw
    from the depositing PLAYER's own Player.lumen_crystals wallet (not cargo
    — it's a ledger resource, unlike ore/equipment). Amounts are rejected past
    the ship's hold contents or the phase's remaining requirement — no single
    call, and no sum of calls, can exceed the per-phase total (warp-gates.md
    "Material staging")."""
    site = _resolve_site(db, site_id, lock=True)
    if site.status != GateConstructionSiteStatus.STAGING:
        raise WarpGateError(
            400,
            f"This site is {site.status.value.lower()} — materials can no "
            "longer be staged into it",
        )
    beacon = db.query(WarpGateBeacon).filter(WarpGateBeacon.id == site.beacon_id).first()
    if beacon is None:
        raise WarpGateError(404, "This site's beacon no longer exists")

    player = _lock_player(db, player.id)
    if player.is_docked or player.is_landed:
        raise WarpGateError(
            400, "You must be in open space to stage materials into a construction site"
        )
    if player.current_sector_id != beacon.source_sector_id:
        raise WarpGateError(
            400,
            f"You must be in sector {beacon.source_sector_id} — the "
            "construction site's sector — to stage materials",
        )
    if not player.current_ship_id:
        raise WarpGateError(400, "No active ship selected")
    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id, Ship.owner_id == player.id
    ).first()
    if ship is None:
        raise WarpGateError(404, "No active ship found")

    ore = int(amounts.get("ore") or 0)
    equipment = int(amounts.get("equipment") or 0)
    lumen = int(amounts.get("lumen_crystals") or 0)
    if ore < 0 or equipment < 0 or lumen < 0:
        raise WarpGateError(400, "Staged amounts cannot be negative")
    if ore == 0 and equipment == 0 and lumen == 0:
        raise WarpGateError(400, "Specify at least one commodity amount to stage")

    remaining_ore = site.required_ore - site.staged_ore
    remaining_equipment = site.required_equipment - site.staged_equipment
    remaining_lumen = site.required_lumen - site.staged_lumen
    if ore > remaining_ore:
        raise WarpGateError(400, f"Only {remaining_ore:,} more ore is needed for this phase")
    if equipment > remaining_equipment:
        raise WarpGateError(
            400, f"Only {remaining_equipment:,} more equipment is needed for this phase"
        )
    if lumen > remaining_lumen:
        raise WarpGateError(
            400, f"Only {remaining_lumen:,} more Lumen Crystals are needed for this phase"
        )

    # Ship-hold / wallet affordability — bespoke messages, since `ore` /
    # `equipment` here are this CALL's amount, not the phase's full total
    # (a "this phase requires X" message would mislead).
    contents = _cargo_contents(ship).get("contents", {})
    if ore:
        have_ore = int(contents.get("ore", 0) or 0)
        if have_ore < ore:
            raise WarpGateError(
                400, f"Your ship's cargo holds only {have_ore:,} ore; you tried to stage {ore:,}"
            )
    if equipment:
        have_equipment = int(contents.get("equipment", 0) or 0)
        if have_equipment < equipment:
            raise WarpGateError(
                400,
                f"Your ship's cargo holds only {have_equipment:,} equipment; "
                f"you tried to stage {equipment:,}",
            )
    if lumen:
        have_lumen = int(getattr(player, "lumen_crystals", 0) or 0)
        if have_lumen < lumen:
            raise WarpGateError(
                400, f"You have {have_lumen:,} Lumen Crystals; this deposit needs {lumen:,}"
            )

    # All checks passed — commit atomically.
    cargo_needs = {k: v for k, v in {"ore": ore, "equipment": equipment}.items() if v}
    if cargo_needs:
        _charge_cargo(ship, cargo_needs)
    if lumen:
        player.lumen_crystals = int(getattr(player, "lumen_crystals", 0) or 0) - lumen

    site.staged_ore += ore
    site.staged_equipment += equipment
    site.staged_lumen += lumen
    db.flush()

    logger.info(
        "Player %s staged %d ore / %d equipment / %d Lumen into construction "
        "site %s (beacon %s, phase %d)",
        player.id, ore, equipment, lumen, site.id, site.beacon_id, site.phase,
    )
    return {
        "site_id": str(site.id),
        "phase": site.phase,
        "status": site.status.value,
        "required": {
            "ore": site.required_ore,
            "equipment": site.required_equipment,
            "lumen_crystals": site.required_lumen,
        },
        "staged": {
            "ore": site.staged_ore,
            "equipment": site.staged_equipment,
            "lumen_crystals": site.staged_lumen,
        },
    }


def advance_construction(db: Session, player: Player, site_id: str) -> Dict[str, Any]:
    """ADR-0078 — commit a fully-staged phase: CONSTRUCTION_TURN_COST (5)
    turns, starts the phase's PHASE_CURE_HOURS (24 canonical hours, via
    scaled_deadline — mirrors BEACON_WINDOW_HOURS above) cure. Owner-only:
    staging materials is a team effort (stage_materials), but committing the
    builder's own turns is the beacon owner's call. A site already CURING or
    READY rejects (no additional turn charge) — the lazy cure-advance below is
    what surfaces a finished cure, not a repeat call here."""
    now = datetime.now(UTC)
    site = _resolve_site(db, site_id, lock=True)
    beacon = db.query(WarpGateBeacon).filter(WarpGateBeacon.id == site.beacon_id).first()
    if beacon is None or beacon.player_id != player.id:
        raise WarpGateError(404, "Construction site not found")

    player = _lock_player(db, player.id)
    _lazy_advance_site_cure(db, site, now)

    if site.status == GateConstructionSiteStatus.CONSUMED:
        raise WarpGateError(400, "This phase's materials have already been committed")
    if site.status == GateConstructionSiteStatus.CANCELLED:
        raise WarpGateError(400, "This construction site was cancelled")
    if site.status == GateConstructionSiteStatus.READY:
        raise WarpGateError(
            400, "This phase already finished curing — proceed to the next step"
        )
    if site.status == GateConstructionSiteStatus.CURING:
        raise WarpGateError(
            400,
            "Still curing — this phase's materials are already committed and "
            "waiting out the cure",
        )

    # STAGING — require the full material set before spending turns.
    if (
        site.staged_ore < site.required_ore
        or site.staged_equipment < site.required_equipment
        or site.staged_lumen < site.required_lumen
    ):
        raise WarpGateError(
            400,
            "Materials are not fully staged yet — "
            f"{site.staged_ore:,}/{site.required_ore:,} ore, "
            f"{site.staged_equipment:,}/{site.required_equipment:,} equipment, "
            f"{site.staged_lumen:,}/{site.required_lumen:,} Lumen Crystals",
        )
    if player.turns < CONSTRUCTION_TURN_COST:
        raise WarpGateError(
            400,
            f"Advancing construction costs {CONSTRUCTION_TURN_COST} turns; "
            f"you have {player.turns}",
        )

    spend_turns(player, CONSTRUCTION_TURN_COST)
    site.turns_applied += CONSTRUCTION_TURN_COST
    site.cure_completes_at = scaled_deadline(PHASE_CURE_HOURS, now)
    site.status = GateConstructionSiteStatus.CURING
    db.flush()

    logger.info(
        "Player %s advanced construction site %s (beacon %s, phase %d) — "
        "curing until %s",
        player.id, site.id, site.beacon_id, site.phase,
        site.cure_completes_at.isoformat(),
    )
    return {
        "site_id": str(site.id),
        "phase": site.phase,
        "status": site.status.value,
        "turns_applied": site.turns_applied,
        "cure_completes_at": site.cure_completes_at.isoformat(),
    }


# --- Phase 3 Step A: anchor focus -------------------------------------------

def _lazy_expire_beacon(db: Session, beacon: WarpGateBeacon, now: Optional[datetime] = None) -> None:
    """An unmatched DEPLOYED beacon expires when its 48h window lapses —
    unless a gate (harmonizing or active) already references it."""
    if beacon.status != WarpGateBeaconStatus.DEPLOYED:
        return
    now = now or datetime.now(UTC)
    if beacon.invulnerable_until is None or _aware(now) < _aware(beacon.invulnerable_until):
        return
    in_progress = (
        db.query(WarpGate)
        .filter(
            WarpGate.beacon_id == beacon.id,
            WarpGate.status.in_([WarpGateStatus.HARMONIZING, WarpGateStatus.ACTIVE]),
        )
        .count()
    )
    if in_progress:
        return
    beacon.status = WarpGateBeaconStatus.EXPIRED
    db.flush()


def _warp_jumper_construction_cost(db: Session) -> int:
    """Build-cost snapshot: WJ spec base_cost (canon 1,000,000) + the two
    10,000 cr phase payments = 1,020,000."""
    spec = db.query(ShipSpecification).filter(
        ShipSpecification.type == ShipType.WARP_JUMPER
    ).first()
    base = spec.base_cost if spec else 1_000_000
    return base + PHASE1_CREDITS + PHASE3_CREDITS


def anchor_focus(db: Session, player: Player, beacon_id: str) -> Dict[str, Any]:
    """Phase 3 Step A — draw the fully-staged, cured Phase-3 construction site
    (ADR-0078), charge turns/credits, freeze the Warp Jumper in HARMONIZING,
    create the gate + FORMING tunnel rows."""
    now = datetime.now(UTC)
    try:
        beacon_uuid = uuid.UUID(str(beacon_id))
    except (ValueError, AttributeError, TypeError):
        raise WarpGateError(404, "Beacon not found")

    # Lock order: beacon/gate row first, player second (see module docstring).
    beacon = (
        db.query(WarpGateBeacon)
        .filter(WarpGateBeacon.id == beacon_uuid)
        .with_for_update()
        .first()
    )
    if beacon is None or beacon.player_id != player.id:
        raise WarpGateError(404, "Beacon not found")

    player = _lock_player(db, player.id)

    _lazy_expire_beacon(db, beacon, now)
    if beacon.status == WarpGateBeaconStatus.EXPIRED:
        raise WarpGateError(
            400,
            "The beacon's 48-hour window has expired — the gate-in-progress "
            "is abandoned (the Quantum Crystal is sunk)",
        )
    if beacon.status != WarpGateBeaconStatus.DEPLOYED:
        raise WarpGateError(
            400, f"Beacon is {beacon.status.value.lower()} and cannot be anchored"
        )
    in_progress = (
        db.query(WarpGate)
        .filter(
            WarpGate.beacon_id == beacon.id,
            WarpGate.status.in_([WarpGateStatus.HARMONIZING, WarpGateStatus.ACTIVE]),
        )
        .first()
    )
    if in_progress is not None:
        raise WarpGateError(400, "This beacon already has a gate in progress")

    ship = _require_warp_jumper(db, player, "anchor a warp gate focus")
    if player.current_sector_id != beacon.destination_sector_id:
        raise WarpGateError(
            400,
            f"The focus must be anchored in the beacon's destination sector "
            f"{beacon.destination_sector_id} — you are in sector "
            f"{player.current_sector_id}",
        )

    # This exact hull must not already be the anchor of another harmonizing
    # gate (FIX 1). _require_warp_jumper already rejects a HARMONIZING ship,
    # but this guards the data-level invariant directly: one hull anchors at
    # most one gate, so a single Warp Jumper can never mint many gates.
    existing_anchor = (
        db.query(WarpGate)
        .filter(
            WarpGate.anchor_ship_id == ship.id,
            WarpGate.status == WarpGateStatus.HARMONIZING,
        )
        .first()
    )
    if existing_anchor is not None:
        raise WarpGateError(
            400, "That hull is already harmonizing into a gate focus"
        )

    source = _sector_by_number(db, beacon.source_sector_id)
    destination = _sector_by_number(db, beacon.destination_sector_id)
    if source is None or destination is None:
        raise WarpGateError(404, "A gate endpoint sector no longer exists")

    # Re-run destination-side validations — the travel window is long enough
    # for a competing gate to fill the cap or an event to flag the sector
    # (warp-gates.md "Placement constraints"). At anchor time both caps count
    # HARMONIZING projects too (FIX 4) so 6 concurrent anchors can't all
    # complete past a cap of 5; completion-time re-validation is the backstop.
    _check_special_features(destination, "Destination")
    _check_incoming_gate_cap(db, destination.sector_id, include_harmonizing=True)
    _check_gate_cap(db, player, include_harmonizing=True)

    if player.turns < PHASE3_TURNS:
        raise WarpGateError(
            400, f"Anchoring the focus costs {PHASE3_TURNS} turns; you have {player.turns}"
        )
    if player.credits < PHASE3_CREDITS:
        raise WarpGateError(
            400,
            f"Anchoring the focus costs {PHASE3_CREDITS:,} credits; "
            f"you have {player.credits:,}",
        )
    # ADR-0078 — the 1,000 ore + 500 equipment + 30 Lumen Crystal draw comes
    # from the Phase-3 construction site (staged + cured over multiple
    # stage-materials / advance-construction cycles), never the Warp Jumper's
    # hold. The site only exists once the Phase-1 site has cured (canon
    # "before the next phase opens" — _lazy_advance_site_cure opens it).
    phase3_site = (
        db.query(GateConstructionSite)
        .filter(GateConstructionSite.beacon_id == beacon.id, GateConstructionSite.phase == 3)
        .with_for_update()
        .first()
    )
    if phase3_site is None:
        raise WarpGateError(
            400,
            "The origin structure hasn't finished curing yet — Phase 1's "
            "construction site must reach its 24h cure before Phase 3 "
            "materials can stage",
        )
    _lazy_advance_site_cure(db, phase3_site, now)
    if phase3_site.status == GateConstructionSiteStatus.CURING:
        raise WarpGateError(
            400,
            "The destination materials are still curing — wait out the 24h "
            "cure before anchoring the focus",
        )
    if phase3_site.status != GateConstructionSiteStatus.READY:
        raise WarpGateError(
            400,
            "The destination materials are not fully staged yet — "
            f"{phase3_site.staged_ore:,}/{phase3_site.required_ore:,} ore, "
            f"{phase3_site.staged_equipment:,}/{phase3_site.required_equipment:,} equipment, "
            f"{phase3_site.staged_lumen:,}/{phase3_site.required_lumen:,} Lumen Crystals",
        )

    # Charge Phase 3 (refundable on cancel — ADR-0029). Turns/credits are
    # UNCHANGED; ore/equipment/Lumen draw from the already-staged, already-
    # cured site instead of the Warp Jumper's hold (ADR-0078).
    spend_turns(player, PHASE3_TURNS)
    player.credits -= PHASE3_CREDITS
    phase3_site.staged_ore = 0
    phase3_site.staged_equipment = 0
    phase3_site.staged_lumen = 0
    phase3_site.status = GateConstructionSiteStatus.CONSUMED

    completes_at = scaled_deadline(HARMONIZATION_HOURS, now)
    ship.status = ShipStatus.HARMONIZING
    ship.harmonization_completes_at = completes_at

    gate = WarpGate(
        beacon_id=beacon.id,
        player_id=player.id,
        status=WarpGateStatus.HARMONIZING,
        harmonization_completes_at=completes_at,
        anchor_ship_id=ship.id,
        construction_cost=_warp_jumper_construction_cost(db),
    )
    db.add(gate)
    db.flush()
    phase3_site.gate_id = gate.id
    db.flush()

    # The tunnel row exists NOW in FORMING (canon names the pre-active state
    # INITIALIZING; the WarpTunnelStatus enum has no such value — FORMING is
    # the closest shipped semantic, flagged in the run report).
    tunnel = WarpTunnel(
        name=f"{source.name} Gate to {destination.name}",
        origin_sector_id=source.id,
        destination_sector_id=destination.id,
        type=WarpTunnelType.ARTIFICIAL,
        status=WarpTunnelStatus.FORMING,
        is_bidirectional=False,
        stability=1.0,
        turn_cost=0,
        energy_cost=0,
        is_public=True,
        created_by_player_id=player.id,
        properties={
            "length": 0.0,
            "stability_rating": 100,
            "expected_lifetime": None,
            "age": 0,
            "traversal_cost": 0,
            "cool_down": 0,
            "discovered": True,
            "discoverer_id": str(player.id),
            "discovery_date": now.isoformat(),
            "affected_by_storms": False,
        },
        artificial_data={
            "beacon_id": str(beacon.id),
            "gate_id": str(gate.id),
            "build_phases": {
                "beacon_deployed_at": beacon.created_at.isoformat() if beacon.created_at else None,
                "anchor_committed_at": now.isoformat(),
                "harmonization_completes_at": completes_at.isoformat(),
            },
        },
    )
    db.add(tunnel)
    db.flush()
    gate.warp_tunnel_id = tunnel.id
    db.flush()

    logger.info(
        "Player %s anchored warp gate focus: gate %s harmonizing until %s",
        player.id, gate.id, completes_at.isoformat(),
    )
    return {"gate": gate, "harmonization_completes_at": completes_at}


# --- Phase 3 Step B: lazy harmonization completion ---------------------------

def _refund_phase3_and_cancel(
    db: Session, gate: WarpGate, ship: Optional[Ship], player: Optional[Player]
) -> Dict[str, int]:
    """ADR-0029 canonical Phase-3 refund path, shared by cancel and the
    completion-time re-validation failure (FIX 2a).

    Turns/credits return to the PLAYER. Per ADR-0078, ore/equipment/Lumen
    return to the Phase-3 CONSTRUCTION SITE, not the ship's hold — the ship
    never had this cargo deducted under the staging model (it left the site,
    not the hold), and warp-gates.md's own Phase 3 failure-mode wording is
    explicit: "All Phase 3 materials ... refund to the construction site /
    player". Refilling the site to its full, already-cured totals means the
    beacon owner can retry anchor-focus without re-ferrying or re-curing.

    Exits the ship HARMONIZING -> IN_SPACE (harmonization_completes_at
    cleared), deletes the FORMING tunnel, marks the gate CANCELLED. The
    BEACON is left DEPLOYED so the player can re-attempt within its window.
    Caller owns locking and the commit. Returns the refund summary."""
    refund = {
        "turns": PHASE3_TURNS,
        "credits": PHASE3_CREDITS,
        "ore": PHASE3_ORE,
        "equipment": PHASE3_EQUIPMENT,
        "lumen_crystals": PHASE3_LUMEN_CRYSTALS,
    }
    if player is not None:
        refund_turns(player, PHASE3_TURNS)
        player.credits += PHASE3_CREDITS

    site = (
        db.query(GateConstructionSite)
        .filter(GateConstructionSite.beacon_id == gate.beacon_id, GateConstructionSite.phase == 3)
        .with_for_update()
        .first()
    )
    if site is not None:
        site.staged_ore = site.required_ore
        site.staged_equipment = site.required_equipment
        site.staged_lumen = site.required_lumen
        site.status = GateConstructionSiteStatus.READY
        site.gate_id = None
        db.flush()
    else:
        logger.warning(
            "Phase-3 construction site missing for gate %s during refund", gate.id
        )

    if ship is not None and not ship.is_destroyed:
        ship.status = ShipStatus.IN_SPACE
        ship.harmonization_completes_at = None

    if gate.warp_tunnel_id:
        tunnel = db.query(WarpTunnel).filter(WarpTunnel.id == gate.warp_tunnel_id).first()
        gate.warp_tunnel_id = None
        if tunnel is not None:
            db.delete(tunnel)

    gate.status = WarpGateStatus.CANCELLED
    gate.harmonization_completes_at = None
    db.flush()
    return refund


def advance_gate(db: Session, gate: WarpGate, now: Optional[datetime] = None) -> WarpGate:
    """Lazy completion — the final atomic step of ADR-0029. Consumes the
    anchor Warp Jumper (no insurance, no Cargo Wreck, full non-bound cargo to
    the escape pod at the destination), flips tunnel/gate ACTIVE and the
    beacon MATCHED. Caller owns the commit.

    Three failure modes are handled at completion (FIX 2):
      (a) destination became no_warp OR its ACTIVE-gate cap filled while we
          harmonized -> canonical Phase-3 refund, gate CANCELLED, beacon
          stays DEPLOYED (warp-gates.md Phase 3 failure modes).
      (b) anchor hull lost mid-harmonization (None, or combat-destroyed
          rather than our own WARP_GATE_ANCHOR dismantle) -> gate CANCELLED,
          tunnel deleted, beacon STAYS DEPLOYED in its window, NO Phase-3
          refund (the materials went down with the hull) (ADR-0029).
      (c) only flip to ACTIVE/MATCHED once destroy_ship succeeds in THIS
          transaction."""
    if gate.status != WarpGateStatus.HARMONIZING:
        return gate
    now = now or datetime.now(UTC)
    if gate.harmonization_completes_at is None or _aware(now) < _aware(gate.harmonization_completes_at):
        return gate

    ship = db.query(Ship).filter(Ship.id == gate.anchor_ship_id).first()

    # (b) Anchor hull was lost mid-harmonization. None = row gone; or
    # is_destroyed with a cause other than our own planned dismantle means a
    # competing path (combat) killed it. Per ADR-0029 the gate does NOT
    # complete: cancel it, delete the FORMING tunnel, leave the beacon
    # DEPLOYED within its window, and grant NO Phase-3 refund — those
    # materials were aboard the lost hull.
    if ship is None or (ship.is_destroyed and ship.destruction_cause != "WARP_GATE_ANCHOR"):
        if gate.warp_tunnel_id:
            tunnel = db.query(WarpTunnel).filter(WarpTunnel.id == gate.warp_tunnel_id).first()
            gate.warp_tunnel_id = None
            if tunnel is not None:
                db.delete(tunnel)
        gate.status = WarpGateStatus.CANCELLED
        gate.harmonization_completes_at = None
        db.flush()
        logger.info(
            "Warp gate %s anchor hull lost mid-harmonization — gate CANCELLED, "
            "beacon stays deployed, no Phase-3 refund (ADR-0029)", gate.id,
        )
        return gate

    # Already consumed by an earlier pass of this same completion (idempotent
    # re-entry under concurrent locks): nothing left to do but report ACTIVE
    # if the flip happened, else fall through to finish.
    if ship.is_destroyed and ship.destruction_cause == "WARP_GATE_ANCHOR" \
            and gate.status == WarpGateStatus.ACTIVE:
        return gate

    # (a) Re-validate the destination at completion. The harmonization window
    # is long enough for the destination to be flagged no_warp or for its
    # incoming ACTIVE-gate cap to fill (count ACTIVE only here — the live cap).
    # On failure run the canonical Phase-3 refund and cancel the gate; the
    # beacon stays deployed for a fresh attempt.
    beacon = db.query(WarpGateBeacon).filter(WarpGateBeacon.id == gate.beacon_id).first()
    destination = (
        _sector_by_number(db, beacon.destination_sector_id) if beacon is not None else None
    )
    revalidation_error: Optional[str] = None
    if destination is None:
        revalidation_error = "destination sector no longer exists"
    else:
        features = destination.special_features or []
        if NO_WARP_FEATURE in features:
            revalidation_error = f"destination sector {destination.sector_id} became a no-warp zone"
        else:
            active_incoming = (
                db.query(WarpGate)
                .join(WarpGateBeacon, WarpGate.beacon_id == WarpGateBeacon.id)
                .filter(
                    WarpGateBeacon.destination_sector_id == destination.sector_id,
                    WarpGate.status == WarpGateStatus.ACTIVE,
                )
                .count()
            )
            if active_incoming >= MAX_INCOMING_ACTIVE_GATES:
                revalidation_error = (
                    f"destination sector {destination.sector_id} reached its "
                    f"incoming-gate cap of {MAX_INCOMING_ACTIVE_GATES} during "
                    "harmonization"
                )

    if revalidation_error is not None:
        # Lock the owner player row before mutating their balances (FIX 6).
        player = (
            db.query(Player)
            .filter(Player.id == gate.player_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        _refund_phase3_and_cancel(db, gate, ship, player)
        logger.info(
            "Warp gate %s failed completion re-validation (%s) — Phase-3 "
            "refunded, gate CANCELLED, beacon stays deployed (ADR-0029)",
            gate.id, revalidation_error,
        )
        return gate

    # (c) Consume the hull FIRST; only flip tunnel/gate/beacon ACTIVE after
    # destroy_ship succeeds in this same transaction. Lock the owner player
    # row before destroy_ship reseats them into the escape pod (FIX 6).
    db.query(Player).filter(Player.id == ship.owner_id) \
        .populate_existing().with_for_update().first()
    # ShipService handles pod ejection AT ship.sector_id — the WJ is
    # physically in the destination sector, which is exactly where canon
    # puts the pilot (ADR-0029 "pilot ejects to destination").
    ShipService(db).destroy_ship(ship, cause="warp_gate_anchor")
    ship.status = ShipStatus.DESTROYED
    ship.harmonization_completes_at = None
    db.flush()

    if gate.warp_tunnel_id:
        tunnel = db.query(WarpTunnel).filter(WarpTunnel.id == gate.warp_tunnel_id).first()
        if tunnel is not None:
            tunnel.status = WarpTunnelStatus.ACTIVE

    if beacon is not None:
        beacon.status = WarpGateBeaconStatus.MATCHED
        beacon.invulnerable_until = None

    gate.status = WarpGateStatus.ACTIVE
    # warp-gates.md:167/230/333 + ADR-0011: the harmonized merged gate has its
    # own 10,000-HP pool (the beacon/focus stay at their 5,000 default).
    gate.hp = 10_000
    db.flush()
    logger.info("Warp gate %s harmonization completed — gate is ACTIVE", gate.id)

    # WO-CD-2 — emergent FACTION rep for building a PUBLIC toll warp gate
    # (CONCRETE-CANON, factions-and-teams.md anti-symmetric matrix: "Build a
    # public toll warp gate | MG +30 | FC +5 | NS +5"; TF/AM/FA/SS/PI 0). This
    # is the single, once-only gate-completion point — the function returns
    # early on every failure / already-completed path above, so reaching here
    # means a real first-time activation (idempotent). Gated on the tunnel being
    # PUBLIC; the private/whitelist matrix row is PARKED (no private-gate build
    # path exists yet — is_public is always True at creation). Flush-only
    # (caller owns the commit), defensive — a rep hiccup never breaks gate
    # completion. The private/whitelist build path, when it lands, should fire a
    # BUILD_PRIVATE_WARP_GATE action (not registered — its row is parked).
    try:
        from src.services.emergent_reputation_service import apply_emergent_action

        tunnel_is_public = True
        if gate.warp_tunnel_id:
            built_tunnel = (
                db.query(WarpTunnel)
                .filter(WarpTunnel.id == gate.warp_tunnel_id)
                .first()
            )
            if built_tunnel is not None:
                tunnel_is_public = bool(built_tunnel.is_public)

        if tunnel_is_public:
            builder = db.query(Player).filter(Player.id == gate.player_id).first()
            if builder is not None:
                apply_emergent_action(
                    db, builder, "BUILD_PUBLIC_WARP_GATE",
                    {"gate_id": str(gate.id)},
                )
    except Exception:
        logger.warning(
            "emergent public-gate faction rep failed for gate %s", gate.id,
            exc_info=True,
        )

    # WO-G10 / ADR-0021 — per-sector faction influence WRITE: a completed
    # warp gate extends the builder's DOMINANT-reputation faction's influence
    # over the gate's DESTINATION sector by +5%. The destination is where the
    # gate plants a permanent foothold; the tunnel's destination_sector_id is
    # already a sectors.id UUID (the influence table's FK target). WRITE half
    # only — read-side taxonomy / patrol-spawn effects are Max-gated and not
    # invoked. Best-effort / flush-only (this function does not commit — the
    # calling route owns the transaction); a hiccup never breaks gate
    # completion. Same once-only activation point as the emergent hook above.
    try:
        from src.services.faction_service import (
            adjust_sector_influence,
            dominant_reputation_faction_id,
        )

        if gate.warp_tunnel_id:
            infl_tunnel = (
                db.query(WarpTunnel)
                .filter(WarpTunnel.id == gate.warp_tunnel_id)
                .first()
            )
            if infl_tunnel is not None and infl_tunnel.destination_sector_id is not None:
                infl_faction_id = dominant_reputation_faction_id(db, gate.player_id)
                if infl_faction_id is not None:
                    adjust_sector_influence(
                        db, infl_tunnel.destination_sector_id, infl_faction_id, 5.0
                    )
    except Exception:
        logger.warning(
            "sector-influence credit failed for completed gate %s", gate.id,
            exc_info=True,
        )

    return gate


def advance_gates_touching_sector(db: Session, sector_number: int, now: Optional[datetime] = None) -> int:
    """Advance every HARMONIZING gate whose beacon touches the given sector
    (called from movement listing/traversal so fresh gates appear without a
    separate poll). Returns the number of gates that completed."""
    now = now or datetime.now(UTC)
    gates = (
        db.query(WarpGate)
        .join(WarpGateBeacon, WarpGate.beacon_id == WarpGateBeacon.id)
        .filter(
            WarpGate.status == WarpGateStatus.HARMONIZING,
            or_(
                WarpGateBeacon.source_sector_id == sector_number,
                WarpGateBeacon.destination_sector_id == sector_number,
            ),
        )
        .populate_existing()
        .with_for_update(of=WarpGate)
        .all()
    )
    advanced = 0
    for gate in gates:
        if advance_gate(db, gate, now).status == WarpGateStatus.ACTIVE:
            advanced += 1
    return advanced


# --- Cancel -------------------------------------------------------------------

def _dispose_beacon_construction_sites(
    db: Session, player: Player, beacon: WarpGateBeacon
) -> Dict[str, int]:
    """ADR-0078 staged-material disposition on a beacon cancel (NO-CANON —
    ADR-0029 only settles the ORIGINAL Phase-1-sunk rule; it is silent on
    staged-but-undrawn materials sitting in this beacon's construction
    site(s) under the new staging model). Builder-proposed disposition,
    flagged to DECISIONS:
      - Phase-1 site: whatever is staged is SUNK along with the rest of
        Phase 1 (ADR-0029's existing rule, verbatim — the site is simply
        where those materials now sit; nothing returns).
      - Phase-3 site (only exists once the Phase-1 site has cured): staged
        ore/equipment return to the CANCELLING PLAYER's active ship hold up
        to its remaining capacity; any excess is forfeited — no warp-gate
        salvage-wreck mechanic exists in this codebase to spawn instead.
        Staged Lumen Crystals always refund in full to Player.lumen_crystals
        (a wallet ledger, no capacity concept).
    Returns the amounts actually returned to the player, for the cancel
    response's `refunded` field."""
    returned = {"ore": 0, "equipment": 0, "lumen_crystals": 0}
    sites = (
        db.query(GateConstructionSite)
        .filter(GateConstructionSite.beacon_id == beacon.id)
        .with_for_update()
        .all()
    )
    ship = None
    if player.current_ship_id:
        ship = db.query(Ship).filter(
            Ship.id == player.current_ship_id, Ship.owner_id == player.id
        ).first()

    for site in sites:
        if site.status in (GateConstructionSiteStatus.CONSUMED, GateConstructionSiteStatus.CANCELLED):
            continue
        if site.phase == 3:
            if site.staged_lumen:
                player.lumen_crystals = int(getattr(player, "lumen_crystals", 0) or 0) + site.staged_lumen
                returned["lumen_crystals"] += site.staged_lumen
                site.staged_lumen = 0
            if ship is not None and not ship.is_destroyed and (site.staged_ore or site.staged_equipment):
                applied = _refund_cargo_up_to_capacity(
                    ship, {"ore": site.staged_ore, "equipment": site.staged_equipment}
                )
                for key in ("ore", "equipment"):
                    took = applied.get(key, 0)
                    if took:
                        returned[key] += took
                        setattr(site, f"staged_{key}", getattr(site, f"staged_{key}") - took)
            if site.staged_ore or site.staged_equipment:
                logger.info(
                    "Beacon %s cancel: %d ore / %d equipment staged in Phase-3 "
                    "site %s forfeited (no salvage mechanic — NO-CANON)",
                    beacon.id, site.staged_ore, site.staged_equipment, site.id,
                )
        site.status = GateConstructionSiteStatus.CANCELLED
    db.flush()
    return returned


def cancel(db: Session, player: Player, gate_or_beacon_id: str) -> Dict[str, Any]:
    """ADR-0029 refund semantics:
    - DEPLOYED beacon -> CANCELLED, Phase 1 materials (incl. the Crystal) sunk
      (see _dispose_beacon_construction_sites for the ADR-0078 staged-site
      disposition this now also covers).
    - HARMONIZING gate -> full Phase 3 refund (turns, credits to the player;
      ore/equipment/Lumen back into the Phase-3 construction site per
      _refund_phase3_and_cancel), ship exits HARMONIZING intact, tunnel row
      deleted. The Phase 1 Crystal never refunds."""
    now = datetime.now(UTC)
    try:
        target_uuid = uuid.UUID(str(gate_or_beacon_id))
    except (ValueError, AttributeError, TypeError):
        raise WarpGateError(404, "Warp gate project not found")

    # Lock order: gate/beacon first, player second.
    gate = (
        db.query(WarpGate)
        .filter(WarpGate.id == target_uuid)
        .with_for_update()
        .first()
    )
    if gate is not None:
        if gate.player_id != player.id:
            raise WarpGateError(404, "Warp gate project not found")
        player = _lock_player(db, player.id)
        # The timer may already have fired — completion outranks cancel.
        advance_gate(db, gate, now)
        if gate.status == WarpGateStatus.ACTIVE:
            raise WarpGateError(
                400, "Harmonization already completed — the gate is active "
                     "and can no longer be cancelled",
            )
        if gate.status != WarpGateStatus.HARMONIZING:
            raise WarpGateError(
                400, f"Gate is {gate.status.value.lower()} and cannot be cancelled"
            )

        ship = db.query(Ship).filter(Ship.id == gate.anchor_ship_id).first()
        refund = _refund_phase3_and_cancel(db, gate, ship, player)
        logger.info("Player %s cancelled harmonizing gate %s (Phase 3 refunded)", player.id, gate.id)
        return {
            "cancelled": "gate",
            "refunded": refund,
            "message": "Harmonization aborted — Phase 3 materials refunded; "
                       "your Warp Jumper is intact. The beacon remains "
                       "deployed for another attempt within its window",
        }

    beacon = (
        db.query(WarpGateBeacon)
        .filter(WarpGateBeacon.id == target_uuid)
        .with_for_update()
        .first()
    )
    if beacon is None or beacon.player_id != player.id:
        raise WarpGateError(404, "Warp gate project not found")
    player = _lock_player(db, player.id)

    _lazy_expire_beacon(db, beacon, now)
    if beacon.status != WarpGateBeaconStatus.DEPLOYED:
        raise WarpGateError(
            400, f"Beacon is {beacon.status.value.lower()} and cannot be cancelled"
        )
    in_progress = (
        db.query(WarpGate)
        .filter(
            WarpGate.beacon_id == beacon.id,
            WarpGate.status == WarpGateStatus.HARMONIZING,
        )
        .first()
    )
    if in_progress is not None:
        raise WarpGateError(
            400, "Cancel the harmonizing gate first — the beacon is bound to "
                 "an anchor in progress",
        )

    returned = _dispose_beacon_construction_sites(db, player, beacon)
    beacon.status = WarpGateBeaconStatus.CANCELLED
    db.flush()
    logger.info(
        "Player %s cancelled beacon %s (Phase 1 materials sunk; staged "
        "Phase-3 materials returned: %s)", player.id, beacon.id, returned,
    )
    message = (
        "Beacon cancelled — Phase 1 materials (including the Quantum "
        "Crystal) are sunk per canon"
    )
    if any(returned.values()):
        message += (
            "; staged Phase-3 materials returned to your ship's hold / "
            "Lumen wallet where capacity allowed (any excess forfeited)"
        )
    return {
        "cancelled": "beacon",
        "refunded": returned,
        "message": message,
    }


# --- Read paths ---------------------------------------------------------------

def _phase_for(beacon: WarpGateBeacon, gate: Optional[WarpGate]) -> str:
    if gate is not None:
        if gate.status == WarpGateStatus.ACTIVE:
            return "ACTIVE"
        if gate.status == WarpGateStatus.HARMONIZING:
            return "HARMONIZING"
    if beacon.status == WarpGateBeaconStatus.DEPLOYED:
        return "BEACON_DEPLOYED"
    if beacon.status == WarpGateBeaconStatus.MATCHED:
        return "ACTIVE"
    if beacon.status == WarpGateBeaconStatus.EXPIRED:
        return "EXPIRED"
    return "CANCELLED"


def _active_construction_site(
    db: Session, beacon_id, now: datetime
) -> Optional[GateConstructionSite]:
    """The construction site currently relevant to staging/advancing this
    beacon's build — the Phase-3 site once it exists (opened once Phase 1
    cures), else the Phase-1 site. Lazily advances every site's cure first
    (a Phase-1 site reaching READY here may open the Phase-3 site as a side
    effect, so sites are re-fetched afterward to pick that up on the same
    read). None once the relevant site is CONSUMED or CANCELLED — nothing
    left to stage."""
    sites = db.query(GateConstructionSite).filter(GateConstructionSite.beacon_id == beacon_id).all()
    for site in sites:
        _lazy_advance_site_cure(db, site, now)
    sites = db.query(GateConstructionSite).filter(GateConstructionSite.beacon_id == beacon_id).all()
    live = {
        s.phase: s for s in sites
        if s.status not in (GateConstructionSiteStatus.CONSUMED, GateConstructionSiteStatus.CANCELLED)
    }
    return live.get(3) or live.get(1)


def _construction_site_payload(site: Optional[GateConstructionSite]) -> Optional[Dict[str, Any]]:
    if site is None:
        return None
    return {
        "site_id": str(site.id),
        "phase": site.phase,
        "status": site.status.value,
        "required": {
            "ore": site.required_ore,
            "equipment": site.required_equipment,
            "lumen_crystals": site.required_lumen,
        },
        "staged": {
            "ore": site.staged_ore,
            "equipment": site.staged_equipment,
            "lumen_crystals": site.staged_lumen,
        },
        "turns_applied": site.turns_applied,
        "cure_completes_at": site.cure_completes_at.isoformat() if site.cure_completes_at else None,
    }


def list_player_projects(db: Session, player: Player) -> List[Dict[str, Any]]:
    """Every gate project the player owns, lazily advanced/expired on read."""
    now = datetime.now(UTC)
    beacons = (
        db.query(WarpGateBeacon)
        .filter(WarpGateBeacon.player_id == player.id)
        .order_by(WarpGateBeacon.created_at.desc())
        .all()
    )
    sector_names: Dict[int, str] = {}

    def name_of(sector_number: int) -> Optional[str]:
        if sector_number not in sector_names:
            sector = _sector_by_number(db, sector_number)
            sector_names[sector_number] = sector.name if sector else None
        return sector_names[sector_number]

    projects: List[Dict[str, Any]] = []
    for beacon in beacons:
        # Locked + populate_existing: advance_gate mutates ships/tunnels and
        # destroys the anchor hull — two concurrent /mine polls (or a poll
        # racing a movement read) must not both see HARMONIZING off a stale
        # identity-mapped row and double-complete (double escape pod, FIX 3).
        gates = (
            db.query(WarpGate)
            .filter(WarpGate.beacon_id == beacon.id)
            .order_by(WarpGate.created_at.desc())
            .populate_existing()
            .with_for_update()
            .all()
        )
        for gate in gates:
            advance_gate(db, gate, now)
        _lazy_expire_beacon(db, beacon, now)
        # The newest non-cancelled gate represents the project's Phase 3 state.
        gate = next((g for g in gates if g.status != WarpGateStatus.CANCELLED), None)
        active_site = _active_construction_site(db, beacon.id, now)
        projects.append({
            "beacon_id": str(beacon.id),
            "gate_id": str(gate.id) if gate else None,
            "phase": _phase_for(beacon, gate),
            "source_sector_id": beacon.source_sector_id,
            "source_name": name_of(beacon.source_sector_id),
            "destination_sector_id": beacon.destination_sector_id,
            "destination_name": name_of(beacon.destination_sector_id),
            "invulnerable_until": beacon.invulnerable_until.isoformat() if beacon.invulnerable_until else None,
            "harmonization_completes_at": (
                gate.harmonization_completes_at.isoformat()
                if gate and gate.harmonization_completes_at else None
            ),
            "created_at": beacon.created_at.isoformat() if beacon.created_at else None,
            "construction_site": _construction_site_payload(active_site),
        })
    return projects


def list_sector_structures(db: Session, sector_number: int) -> Dict[str, Any]:
    """Beacons and active outbound gates visible in a sector (gates are
    'visible to all in source sector' per canon). Lazily advances first."""
    now = datetime.now(UTC)
    advance_gates_touching_sector(db, sector_number, now)

    beacon_rows = (
        db.query(WarpGateBeacon)
        .filter(
            WarpGateBeacon.source_sector_id == sector_number,
            WarpGateBeacon.status == WarpGateBeaconStatus.DEPLOYED,
        )
        .all()
    )
    beacons = []
    for beacon in beacon_rows:
        _lazy_expire_beacon(db, beacon, now)
        if beacon.status != WarpGateBeaconStatus.DEPLOYED:
            continue
        owner = db.query(Player).filter(Player.id == beacon.player_id).first()
        destination = _sector_by_number(db, beacon.destination_sector_id)
        beacons.append({
            "beacon_id": str(beacon.id),
            "owner_name": owner.username if owner else None,
            "destination_sector_id": beacon.destination_sector_id,
            "destination_name": destination.name if destination else None,
            "invulnerable_until": beacon.invulnerable_until.isoformat() if beacon.invulnerable_until else None,
            "hp": beacon.hp,
        })

    gate_rows = (
        db.query(WarpGate)
        .join(WarpGateBeacon, WarpGate.beacon_id == WarpGateBeacon.id)
        .filter(
            WarpGateBeacon.source_sector_id == sector_number,
            WarpGate.status == WarpGateStatus.ACTIVE,
        )
        .all()
    )
    gates = []
    for gate in gate_rows:
        beacon = gate.beacon
        owner = db.query(Player).filter(Player.id == gate.player_id).first()
        destination = _sector_by_number(db, beacon.destination_sector_id)
        tunnel = (
            db.query(WarpTunnel).filter(WarpTunnel.id == gate.warp_tunnel_id).first()
            if gate.warp_tunnel_id else None
        )
        gates.append({
            "gate_id": str(gate.id),
            "destination_sector_id": beacon.destination_sector_id,
            "destination_name": destination.name if destination else None,
            "owner_name": owner.username if owner else None,
            "is_public": tunnel.is_public if tunnel is not None else True,
            "access_mode": _access_mode_of(tunnel) if tunnel is not None else ACCESS_MODE_PUBLIC,
            # Toll system is design-only (warp-gates.md) — all gates are
            # toll-free until it ships; the field is part of the contract.
            "toll": 0,
        })

    return {"beacons": beacons, "gates": gates}


# --- Access-mode enforcement (WO-DBB-WG1) -----------------------------------

def _access_mode_of(tunnel: Optional[WarpTunnel]) -> str:
    """Read the authoritative access mode from a gate's WarpTunnel row.

    The mode lives in WarpTunnel.access_requirements["mode"]; absent/blank
    falls back to the coarse is_public flag (warp-gates.md: is_public is the
    coarse flag, access_requirements is authoritative). A None tunnel (no
    traversable connection yet) is treated PUBLIC — nothing to gate."""
    if tunnel is None:
        return ACCESS_MODE_PUBLIC
    reqs = tunnel.access_requirements or {}
    mode = reqs.get("mode") if isinstance(reqs, dict) else None
    if mode in ACCESS_MODES:
        return mode
    # No explicit mode set yet — honour the coarse flag.
    return ACCESS_MODE_PUBLIC if tunnel.is_public else ACCESS_MODE_PRIVATE


def _player_team_ids(db: Session, player: Player) -> set:
    """The set of team UUIDs a player currently belongs to (by membership row,
    not only Player.team_id — a player may hold rows for several teams). Always
    includes Player.team_id when set so a freshly-set team is honoured."""
    ids = set()
    if player.team_id:
        ids.add(player.team_id)
    rows = (
        db.query(TeamMember.team_id)
        .filter(TeamMember.player_id == player.id)
        .all()
    )
    for (team_id,) in rows:
        if team_id is not None:
            ids.add(team_id)
    return ids


def check_traversal_access(db: Session, player: Player, tunnel: WarpTunnel) -> None:
    """Enforce the gate's access mode for a player attempting traversal.

    Raises WarpGateError(403, ...) when the player is not allowed. Returns
    None (allowed) for: any non-player gate, the owner, or a player who
    satisfies the configured mode. This is the single enforcement point the
    movement layer calls before letting a player traverse a player-built gate
    (warp-gates.md "Access control"). No locking, no mutation — a pure check.

    The owner is identified by WarpTunnel.created_by_player_id (the gate's
    owner FK on the traversable row, kept in sync with WarpGate.player_id on
    transfer)."""
    # Only player-built gates are access-controlled. A natural/generator tunnel
    # has no created_by_player_id and is always open.
    if tunnel is None or tunnel.created_by_player_id is None \
            or tunnel.type != WarpTunnelType.ARTIFICIAL:
        return

    owner_id = tunnel.created_by_player_id
    if owner_id == player.id:
        return  # the owner always passes

    mode = _access_mode_of(tunnel)
    reqs = tunnel.access_requirements or {}
    if not isinstance(reqs, dict):
        reqs = {}

    if mode == ACCESS_MODE_PUBLIC:
        return

    if mode == ACCESS_MODE_PRIVATE:
        raise WarpGateError(
            403,
            "ERR_GATE_PRIVATE: this warp gate is private — only its owner may "
            "traverse it",
        )

    if mode == ACCESS_MODE_WHITELIST:
        whitelist = {str(x) for x in (reqs.get("whitelist") or [])}
        if str(player.id) in whitelist:
            return
        raise WarpGateError(
            403,
            "ERR_GATE_NOT_WHITELISTED: this warp gate is restricted to a "
            "whitelist you are not on",
        )

    if mode == ACCESS_MODE_TEAM_ONLY:
        owner = db.query(Player).filter(Player.id == owner_id).first()
        owner_teams = _player_team_ids(db, owner) if owner is not None else set()
        if owner_teams & _player_team_ids(db, player):
            return
        raise WarpGateError(
            403,
            "ERR_GATE_TEAM_ONLY: this warp gate is restricted to the owner's "
            "team",
        )

    if mode == ACCESS_MODE_ALLIANCE:
        owner = db.query(Player).filter(Player.id == owner_id).first()
        owner_teams = _player_team_ids(db, owner) if owner is not None else set()
        player_teams = _player_team_ids(db, player)
        if owner_teams & player_teams:
            return  # same team always passes under ALLIANCE too
        # Allied teams are stored as team UUIDs in access_requirements.allies
        # (no diplomacy/alliance table exists yet — this is the documented
        # JSONB interpretation, flagged NO-CANON for structural allies).
        allies = {str(x) for x in (reqs.get("allies") or [])}
        if allies & {str(t) for t in player_teams}:
            return
        raise WarpGateError(
            403,
            "ERR_GATE_ALLIANCE_ONLY: this warp gate is restricted to the "
            "owner's team and its allies",
        )

    # Unknown mode (should be impossible — validated on set) — fail closed.
    raise WarpGateError(403, "ERR_GATE_ACCESS_DENIED: gate access denied")


def _resolve_owned_active_gate(db: Session, player: Player, gate_id: str, *, lock: bool):
    """Look up an ACTIVE WarpGate the player owns, optionally row-locked.

    Ownership-gated: a gate that isn't the caller's 404s (no existence leak,
    mirrors the project endpoints). Only ACTIVE gates expose permissions /
    transfer — an in-construction or collapsed gate has no traversable
    connection to administer."""
    try:
        gate_uuid = uuid.UUID(str(gate_id))
    except (ValueError, AttributeError, TypeError):
        raise WarpGateError(404, "Warp gate not found")
    query = db.query(WarpGate).filter(WarpGate.id == gate_uuid)
    if lock:
        query = query.populate_existing().with_for_update()
    gate = query.first()
    if gate is None or gate.player_id != player.id:
        raise WarpGateError(404, "Warp gate not found")
    if gate.status != WarpGateStatus.ACTIVE:
        raise WarpGateError(
            400,
            f"Gate is {gate.status.value.lower()} — only an active gate can be "
            "administered",
        )
    return gate


def _validate_uuid_list(values, label: str) -> List[str]:
    """Coerce an inbound list of player/team ids to canonical UUID strings,
    rejecting malformed entries and over-long lists."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise WarpGateError(400, f"{label} must be a list of UUIDs")
    if len(values) > MAX_ACCESS_LIST_ENTRIES:
        raise WarpGateError(
            400,
            f"{label} may hold at most {MAX_ACCESS_LIST_ENTRIES} entries",
        )
    out: List[str] = []
    seen = set()
    for value in values:
        try:
            canonical = str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            raise WarpGateError(400, f"{label} contains an invalid UUID: {value!r}")
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def set_gate_permissions(
    db: Session,
    player: Player,
    gate_id: str,
    mode: str,
    whitelist=None,
    allies=None,
) -> Dict[str, Any]:
    """WO-DBB-WG1 — atomically set a gate's access mode, whitelist and allied
    teams (owner-only). Persists onto the gate's WarpTunnel.access_requirements
    JSONB and keeps the coarse is_public flag in sync. Caller owns the commit.

    Lock order: gate row first (then the tunnel is fetched under the same txn).
    No credit movement, so no player lock is needed."""
    mode = (mode or "").upper()
    if mode not in ACCESS_MODES:
        raise WarpGateError(
            400,
            f"Unknown access mode {mode!r}; valid modes: "
            + ", ".join(sorted(ACCESS_MODES)),
        )

    gate = _resolve_owned_active_gate(db, player, gate_id, lock=True)
    if not gate.warp_tunnel_id:
        raise WarpGateError(400, "This gate has no traversable connection to configure")
    tunnel = (
        db.query(WarpTunnel)
        .filter(WarpTunnel.id == gate.warp_tunnel_id)
        .with_for_update()
        .first()
    )
    if tunnel is None:
        raise WarpGateError(404, "The gate's warp tunnel could not be found")

    whitelist_ids = _validate_uuid_list(whitelist, "whitelist")
    allies_ids = _validate_uuid_list(allies, "allies")

    reqs = dict(tunnel.access_requirements or {}) if isinstance(tunnel.access_requirements, dict) else {}
    reqs["mode"] = mode
    reqs["whitelist"] = whitelist_ids
    reqs["allies"] = allies_ids
    tunnel.access_requirements = reqs
    flag_modified(tunnel, "access_requirements")
    tunnel.is_public = mode in _PUBLIC_MODES
    db.flush()

    logger.info(
        "Player %s set warp gate %s access mode to %s (whitelist=%d allies=%d)",
        player.id, gate.id, mode, len(whitelist_ids), len(allies_ids),
    )
    return {
        "gate_id": str(gate.id),
        "mode": mode,
        "whitelist": whitelist_ids,
        "allies": allies_ids,
        "is_public": tunnel.is_public,
    }


# --- Ownership transfer / sale (WO-DBB-WG2) ---------------------------------

def transfer_gate(
    db: Session,
    player: Player,
    gate_id: str,
    new_owner_id: str,
    sale_price: Optional[int] = None,
) -> Dict[str, Any]:
    """WO-DBB-WG2 — atomically transfer an active gate to another player.

    Flips WarpGate.player_id AND the gate's WarpTunnel.created_by_player_id;
    the toll / access-mode / accumulated-revenue config all live on those rows
    (access_requirements + artificial_data JSONB) and carry over untouched.
    Settles an optional salePrice (debit buyer, credit seller) under row locks.
    Enforces the buyer's max-gate cap; on any failure no balance or ownership
    changes (single transaction, reversed by the caller's rollback). Caller
    owns the commit.

    Lock order (shipped race-safety invariant): the GATE row first, then the
    PLAYER rows — and the two players in a deterministic id order so two
    simultaneous transfers between the same pair cannot deadlock."""
    if sale_price is not None:
        if not isinstance(sale_price, int) or isinstance(sale_price, bool):
            raise WarpGateError(400, "salePrice must be a whole number of credits")
        if sale_price < 0:
            raise WarpGateError(400, "salePrice cannot be negative")

    try:
        buyer_uuid = uuid.UUID(str(new_owner_id))
    except (ValueError, AttributeError, TypeError):
        raise WarpGateError(400, "newOwnerId is not a valid player id")
    if buyer_uuid == player.id:
        raise WarpGateError(400, "You already own this gate")

    # Lock the gate row first (lock order: gate before players).
    gate = _resolve_owned_active_gate(db, player, gate_id, lock=True)

    # Lock both player rows in a deterministic id order to avoid deadlock,
    # then bind the named seller/buyer handles to the locked instances.
    ids_in_order = sorted({player.id, buyer_uuid}, key=lambda x: str(x))
    locked: Dict[Any, Player] = {}
    for pid in ids_in_order:
        row = (
            db.query(Player)
            .filter(Player.id == pid)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if row is None:
            raise WarpGateError(
                404,
                "Buyer not found" if pid == buyer_uuid else "Seller not found",
            )
        locked[pid] = row
    seller = locked[player.id]
    buyer = locked[buyer_uuid]

    if buyer.id == seller.id:
        raise WarpGateError(400, "You already own this gate")

    # Buyer-cap check: the buyer's current owned ACTIVE gates + this one must
    # not exceed their cap (warp-gates.md "Transfer & sale" buyer-cap check;
    # ADR-0010 formula via max_gates_for_player). The gate being transferred is
    # the seller's, so it is not yet in the buyer's count.
    buyer_cap = max_gates_for_player(db, buyer)
    buyer_active = (
        db.query(WarpGate)
        .filter(
            WarpGate.player_id == buyer.id,
            WarpGate.status == WarpGateStatus.ACTIVE,
        )
        .count()
    )
    if buyer_active + 1 > buyer_cap:
        raise WarpGateError(
            400,
            f"The buyer already owns {buyer_active} active warp gate(s); their "
            f"limit is {buyer_cap} — they cannot accept another. (No credits "
            "were moved.)",
        )

    # Settle the optional sale price under the held locks. The buyer is debited
    # and the seller credited atomically; if the buyer is short, nothing moves
    # (the caller's rollback reverses any prior flush — "holds reversed").
    settled_price = 0
    if sale_price:
        if buyer.credits < sale_price:
            raise WarpGateError(
                400,
                f"The buyer has {buyer.credits:,} credits but the sale price is "
                f"{sale_price:,} — transfer rejected, no credits moved",
            )
        buyer.credits -= sale_price
        seller.credits += sale_price
        settled_price = sale_price

    # Flip ownership on the gate AND its traversable tunnel together. Toll /
    # access / revenue config stays on the tunnel JSONB and carries over.
    gate.player_id = buyer.id
    tunnel = None
    if gate.warp_tunnel_id:
        tunnel = (
            db.query(WarpTunnel)
            .filter(WarpTunnel.id == gate.warp_tunnel_id)
            .with_for_update()
            .first()
        )
        if tunnel is not None:
            tunnel.created_by_player_id = buyer.id
    # Also reassign the linked beacon (the project row list_player_projects keys
    # /mine off): without this the transferred gate would stay on the SELLER's
    # /mine and never surface on the BUYER's. Locked in the same gate->tunnel->
    # beacon->player order.
    if gate.beacon_id:
        beacon = (
            db.query(WarpGateBeacon)
            .filter(WarpGateBeacon.id == gate.beacon_id)
            .with_for_update()
            .first()
        )
        if beacon is not None:
            beacon.player_id = buyer.id
    db.flush()

    logger.info(
        "Warp gate %s transferred from player %s to player %s (sale_price=%d)",
        gate.id, seller.id, buyer.id, settled_price,
    )
    return {
        "gate_id": str(gate.id),
        "previous_owner_id": str(seller.id),
        "new_owner_id": str(buyer.id),
        "sale_price": settled_price,
        "buyer_credits": buyer.credits,
        "seller_credits": seller.credits,
        "access_carried_over": (
            _access_mode_of(tunnel) if tunnel is not None else ACCESS_MODE_PUBLIC
        ),
    }
