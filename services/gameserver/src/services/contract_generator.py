"""
NPC contract generator -- WO-ECON-CONTRACT-1-KERNEL lane 3, contracts.md's
own build order step 2 ("NPC `cargo_delivery` generator (simplest type) --
proves the lifecycle end-to-end", :426). WO-CONTRACT-3-NPCGEN-TYPES adds two
more of the six previously-ungenerated `contract_type` values: `express_
delivery` (tight-deadline reclassification of an otherwise-ordinary pair --
see `compute_contract_generation_batch`'s own comment) and `hazardous_
transport` (issued at BLACK_MARKET-type destination stations only).
WO-CONTRACT-4-BULK (Lane B) adds a third: `bulk_procurement` (an origin
station genuinely SHORT on live stock -- see BULK_PROCUREMENT_DEFICIT_
THRESHOLD's own comment below; fulfilled downstream via station lockers,
contract_service.py/storage_service.py -- untouched by this generator,
which only classifies + prices + posts the row). The
remaining three (`refugee_transport`, `acquisition_bounty`, `escort`)
still have no generator -- `refugee_transport` needs a `passenger_rating`
ship field that doesn't exist; `acquisition_bounty`/`escort` are
untouched, later build steps.

SYNC Session -- `generate_npc_contracts(db, ...)` is the pure, testable
core; the npc_scheduler_service.py wrapper owns its own SessionLocal +
commit, matching every other scheduler sweep in that module (see
`_run_price_alert_sweep_sync` for the precedent this mirrors).
"""
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.models.contract import Contract, ContractIssuerType, ContractStatus, ContractType
from src.models.faction import Faction
from src.models.sector import Sector, sector_warps
from src.models.station import Station, StationType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.trading_service import TradingService

logger = logging.getLogger(__name__)

# [NO-CANON] pool sizing -- contracts.md:100 says a board's capacity is
# "bounded" ("a Class-0 trade hub posts more [contracts]... than a
# Class-8") but gives no number. A flat per-station cap, independent of
# station class, is this kernel's pin -- a class-weighted cap is a natural
# follow-up once more than one contract_type is generated. Proposed to
# DECISIONS.md.
MAX_ACTIVE_NPC_CONTRACTS_PER_STATION = 5

# [NO-CANON] quantity sizing -- contracts.md never gives a generation-time
# quantity formula (the worked example at :328-340 only ANCHORS one
# instance at 150 units). Flat 20-150 unit band, floored by whatever stock
# the origin station actually has on hand -- never generates a contract
# for more than the station's live quantity.
MIN_CONTRACT_QUANTITY = 20
MAX_CONTRACT_QUANTITY = 150

# [NO-CANON] deadline sizing -- contracts.md:245 pins only the FLOOR
# ("deadline >= 1 hour out"). This kernel generates a WALL-CLOCK window of
# 1-8 hours -- matching this codebase's dominant wall-clock-storage
# convention for timed state (planetary/CRT anchors: "canonical only
# scales ELAPSED, the anchor itself is wall-clock") rather than
# introducing a new GAME_TIME_SCALE-adjusted deadline surface unique to
# contracts.
MIN_DEADLINE_HOURS = 1.0
MAX_DEADLINE_HOURS = 8.0

# Worked-example anchors (contracts.md:328-340): distance_factor = 1.0 +
# 0.05 x hops is the doc's OWN formula (8 hops -> 1.40, verbatim).
# urgency_factor's coefficients below are chosen so a 90-minute deadline
# reproduces the doc's own 1.10 anchor exactly: (4.0 - 1.5) x 0.04 = 0.10.
# The anchor match is a derivation check, not a literal quote -- NO-CANON,
# proposed to DECISIONS.md alongside the sizing constants above.
BASE_RATE = Decimal("1.0")
DISTANCE_FACTOR_PER_HOP = Decimal("0.05")
URGENCY_STANDARD_HOURS = Decimal("4.0")
URGENCY_PER_HOUR = Decimal("0.04")
URGENCY_FACTOR_CEILING = Decimal("2.0")
CARGO_DELIVERY_TYPE_MULTIPLIER = Decimal("1.0")  # contracts.md:346 -- verbatim anchor
PENALTY_MULTIPLIER = Decimal("1.0")  # contracts.md:40 -- default 1.0x payment for cargo

# --- WO-CONTRACT-3-NPCGEN-TYPES: express_delivery / hazardous_transport ---
#
# Canon's own payment formula (contracts.md:312-317) already has a
# `contract_type_multiplier` slot -- CARGO_DELIVERY_TYPE_MULTIPLIER above
# IS that slot for `cargo_delivery` (pinned at the doc's own 1.0 anchor,
# contracts.md:346). The two constants below fill the SAME slot for the
# two new types this WO generates, each grounded in a canon-cited range
# rather than invented from scratch:
#
# EXPRESS_DELIVERY_TYPE_MULTIPLIER -- contracts.md:319: "express deliveries
# pay roughly 1.5-2.0x their non-express equivalents". Pinned at the
# range's midpoint. [NO-CANON: exact point within the cited range,
# proposed to DECISIONS.md.]
EXPRESS_DELIVERY_TYPE_MULTIPLIER = Decimal("1.75")
# EXPRESS_PENALTY_MULTIPLIER -- contracts.md:136: "Express contracts use a
# stricter penalty on failure" (qualitative only, no number given).
# [NO-CANON] 1.5x vs cargo_delivery's 1.0x -- proposed to DECISIONS.md.
EXPRESS_PENALTY_MULTIPLIER = Decimal("1.5")
# EXPRESS_DEADLINE_THRESHOLD_HOURS -- this kernel does NOT draw a second,
# separate deadline for express jobs ("via existing deadline knobs" per
# dispatch): every (origin, commodity, destination) match still draws ONE
# deadline from the existing pick_deadline_hours() (1-8h) band, exactly as
# before. A match is *reclassified* express iff that draw lands at or
# below this threshold -- express literally means "this run happened to
# get a tight deadline", not an independently-rolled type. This keeps
# every EXISTING test's `monkeypatch.setattr(pick_deadline_hours, lambda:
# 3.0)` fixture landing in the cargo_delivery branch byte-identically (3.0
# > 2.0), so this WO adds zero flakiness to the pre-existing suite.
# [NO-CANON] 2.0h threshold -- proposed to DECISIONS.md.
EXPRESS_DEADLINE_THRESHOLD_HOURS = Decimal("2.0")

# HAZARDOUS_TRANSPORT_TYPE_MULTIPLIER -- contracts.md:420: "[hazardous
# transport contracts] pay 2-4x standard rates" (contracts.md:140 also:
# "pays significantly more"). Pinned at the range's midpoint. [NO-CANON:
# exact point within the cited range, proposed to DECISIONS.md.]
HAZARDOUS_TRANSPORT_TYPE_MULTIPLIER = Decimal("3.0")
# Hazardous transport's FAILURE penalty (the `penalty` column, charged on
# abandon()/expiry -- distinct from the completion-time faction penalty
# below) reuses the plain PENALTY_MULTIPLIER (1.0x): canon gives no
# separate number for a stricter hazardous failure penalty the way it
# explicitly does for express (contracts.md:136); not invented here.
#
# HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY -- contracts.md:420: "...and
# apply a faction penalty on completion (the law-side faction loses
# standing)". Stored on `Contract.reputation_penalty` at GENERATION time
# (exactly the column canon's own Reputation Effects section describes --
# contracts.md:369/371: "reputation_reward and reputation_penalty are
# written on the contract row at posting time" -- this is the first real
# writer of that column; contract_service.complete() below is the first
# real READER, gated to hazardous_transport only). [NO-CANON] magnitude:
# no canon number exists for the rep-delta itself; -30 is pinned smaller
# than illegal_commodities.py's own STOLEN_GOODS federation_rep_delta
# (-50) -- a criminal-issued CARGO CONTRACT is one step more indirect than
# directly fencing contraband on the black-market floor. Proposed to
# DECISIONS.md.
HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY = -30

# --- WO-CONTRACT-4-BULK (Lane B): bulk_procurement ---
#
# BULK_PROCUREMENT_TYPE_MULTIPLIER -- contracts.md's own Bulk procurement
# section (:126-132) and its worked walk-away example (:184 -- 500cr for
# 5,000 units) describe quantity/partial-fulfillment mechanics but cite NO
# payment premium the way express_delivery ("roughly 1.5-2.0x", :319) and
# hazardous_transport ("2-4x", :420) each get an explicit multiplier
# range. Reuses the plain 1.0x cargo_delivery slot rather than inventing a
# number canon never gives -- per this WO's own "do not invent a new
# pricing scheme, mirror the siblings" instruction.
BULK_PROCUREMENT_TYPE_MULTIPLIER = Decimal("1.0")
# BULK_PROCUREMENT_PENALTY_MULTIPLIER -- WO-4's Max ruling (design brief
# audit/design-briefs/wo4-bulk-design-2026-07-17.md): the walk-away
# penalty helper's degenerate case (a bulk contract with no locker
# deposits) reads the STATIC `contract.penalty` column directly and
# requires it equal `payment` exactly. `post_player_contract`'s own
# bulk-parity write (contract_service.py:1766) already pins penalty ==
# payment for this type; 1.0x reproduces that exactly here too (same
# multiplier cargo_delivery uses by default).
BULK_PROCUREMENT_PENALTY_MULTIPLIER = Decimal("1.0")
#
# BULK_PROCUREMENT_DEFICIT_THRESHOLD classification -- Max-ruled correction
# (2026-07-17): a bulk_procurement job means a station is SHORT on a
# commodity and wants players to gather + deliver a restock (contracts.md
# :130 -- "Gather N units... from anywhere"), NOT a station that already
# has a surplus to move out (a plain cargo_delivery/express run covers
# that fine). This generator's only per-pair scarcity signal is the
# ORIGIN's own live sell-stock (`available`) -- the loop's own earlier
# gate (`if available < MIN_CONTRACT_QUANTITY: continue`) already floors
# every candidate reaching classification at >= MIN_CONTRACT_QUANTITY, so
# pinning the deficit threshold AT MIN_CONTRACT_QUANTITY would be
# unreachable dead code (my original draft's `> MAX_CONTRACT_QUANTITY`
# surplus check was reachable but pointed the wrong direction -- Max
# caught the inversion). Pinned instead at 2x the floor (`MIN_CONTRACT_
# QUANTITY * 2` = 40, reachable, and a narrow low-end band -- [20, 40) out
# of the full range above 20 -- so a genuinely-thin origin is a MINORITY
# case, not the default, mirroring EXPRESS_DEADLINE_THRESHOLD_HOURS' own
# ~15%-of-range low-end-band proportion). [NO-CANON] the 2x multiplier
# itself -- no separate number invented beyond re-deriving from the
# existing floor constant -- proposed to DECISIONS.md.
BULK_PROCUREMENT_DEFICIT_THRESHOLD = MIN_CONTRACT_QUANTITY * 2
#
# Quantity implication (the real catch team-lead flagged): a deficit-
# triggered bulk contract's DEMAND is the restock amount the station
# needs, NOT bounded by the very-thin `available` that triggered it in
# the first place (canon: the player "sources however they like" --
# "no fixed origin" -- the quantity is a demand figure, not a supply
# figure). Reusing this file's own `quantity = min(MAX_CONTRACT_QUANTITY,
# available)` cap for bulk would silently produce a TINY quantity (<=
# BULK_PROCUREMENT_DEFICIT_THRESHOLD, since `available` is already known
# to be below it) -- semantically broken for a type whose whole premise
# is "more than a single haul." Bulk pins `quantity` at this generator's
# own MAX_CONTRACT_QUANTITY ceiling instead (the SAME per-haul cap every
# other type is capped AT, just not further reduced by this one thin
# origin's reserves) -- reuses an existing constant rather than inventing
# a new, larger bulk-specific quantity band this WO wasn't asked to
# design (per "do not invent a new pricing scheme, mirror the siblings").


def _now() -> datetime:
    return datetime.now(UTC)


def pick_deadline_hours() -> float:
    """Isolated so tests can monkeypatch it for determinism."""
    return random.uniform(MIN_DEADLINE_HOURS, MAX_DEADLINE_HOURS)  # noqa: S311 -- gameplay timing, not crypto


def _compute_typed_contract_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
    type_multiplier: Decimal, penalty_multiplier: Decimal,
) -> Tuple[Decimal, Decimal]:
    """Pure -- no DB, no side effects. Shared core of contracts.md:312-317's
    formula (`payment = base_rate x commodity_value x distance_factor x
    urgency_factor x contract_type_multiplier`) for every `contract_type`
    this generator produces -- `compute_cargo_delivery_payment` /
    `compute_express_delivery_payment` / `compute_hazardous_transport_
    payment` are thin, type-specific wrappers over this ONE formula, never
    three copies of it. `payment` is monotone-increasing in `quantity` by
    construction. Returns (payment, penalty), both quantized to cents."""
    commodity_value = unit_price * Decimal(quantity)
    distance_factor = Decimal("1.0") + DISTANCE_FACTOR_PER_HOP * Decimal(hops)
    tightness = URGENCY_STANDARD_HOURS - deadline_hours
    urgency_factor = Decimal("1.0") + max(Decimal("0"), tightness) * URGENCY_PER_HOUR
    urgency_factor = min(urgency_factor, URGENCY_FACTOR_CEILING)
    payment = (
        BASE_RATE * commodity_value * distance_factor * urgency_factor * type_multiplier
    ).quantize(Decimal("0.01"))
    penalty = (payment * penalty_multiplier).quantize(Decimal("0.01"))
    return payment, penalty


def compute_cargo_delivery_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
) -> Tuple[Decimal, Decimal]:
    """`cargo_delivery`'s own type_multiplier/penalty_multiplier pins,
    unchanged from before this WO -- see `_compute_typed_contract_payment`
    for the shared formula every contract type now runs through."""
    return _compute_typed_contract_payment(
        unit_price, quantity, hops, deadline_hours,
        CARGO_DELIVERY_TYPE_MULTIPLIER, PENALTY_MULTIPLIER,
    )


def compute_express_delivery_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
) -> Tuple[Decimal, Decimal]:
    """`express_delivery`'s type_multiplier/penalty_multiplier pins -- see
    the EXPRESS_* constants' own comments for the canon citations."""
    return _compute_typed_contract_payment(
        unit_price, quantity, hops, deadline_hours,
        EXPRESS_DELIVERY_TYPE_MULTIPLIER, EXPRESS_PENALTY_MULTIPLIER,
    )


def compute_hazardous_transport_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
) -> Tuple[Decimal, Decimal]:
    """`hazardous_transport`'s type_multiplier pin (2-4x canon range,
    contracts.md:420) -- penalty_multiplier stays the plain cargo default,
    see HAZARDOUS_TRANSPORT_TYPE_MULTIPLIER's own comment."""
    return _compute_typed_contract_payment(
        unit_price, quantity, hops, deadline_hours,
        HAZARDOUS_TRANSPORT_TYPE_MULTIPLIER, PENALTY_MULTIPLIER,
    )


def compute_bulk_procurement_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
) -> Tuple[Decimal, Decimal]:
    """`bulk_procurement`'s type_multiplier/penalty_multiplier pins -- see
    the BULK_PROCUREMENT_* constants' own comments. Both multipliers are
    1.0, so payment == cargo_delivery's own result and penalty == payment
    exactly -- the latter is a hard requirement (WO-4's degenerate-case
    walk-away penalty reads this static column directly)."""
    return _compute_typed_contract_payment(
        unit_price, quantity, hops, deadline_hours,
        BULK_PROCUREMENT_TYPE_MULTIPLIER, BULK_PROCUREMENT_PENALTY_MULTIPLIER,
    )


# --- sector-graph helpers (private to this service -- mirrors escape_pod_
# service.py / slipdrive_service.py's own per-service-graph-helper
# convention). DIRECTED (unlike the stranding-recovery trio's undirected
# BFS) -- this hop count represents a REAL delivery run a player must fly,
# which must respect warp directionality, not an out-of-band teleport. ---

def _load_directed_sector_graph(db: Session) -> Tuple[Dict[uuid.UUID, int], Dict[uuid.UUID, List[uuid.UUID]]]:
    """Edge sources mirror the established pair nav_service.NavService.
    _build_known_graph / aria_personal_intelligence_service._build_
    explored_adjacency already use: the ``sector_warps`` association table
    (bang's IN-region warp topology) PLUS ACTIVE ``WarpTunnel`` rows.

    WarpTunnel is not optional here (WO-SWEEP-SILENT-SWEEPS): bang_import_
    service._add_nexus_warp wires every spoke region to the Nexus with a
    WarpTunnel row, not a sector_warps row -- that ONE tunnel per region is
    the entire inter-region graph. Reading sector_warps alone (as this
    function did before) leaves every cross-region origin/destination pair
    unreachable, which -- with a real galaxy spanning many regions -- silently
    zeroed out contract generation for any pair that wasn't in the same
    region. No is_latent filter, unlike a player's own explored-graph view:
    this represents the galaxy's REAL connectivity for NPC-run cargo (see
    this function's own directed-BFS docstring above), not what a player has
    scanned yet."""
    sectors = db.query(Sector.id, Sector.sector_id).all()
    pk_to_sector_id = {s.id: s.sector_id for s in sectors}
    edges = db.query(
        sector_warps.c.source_sector_id,
        sector_warps.c.destination_sector_id,
        sector_warps.c.is_bidirectional,
    ).all()
    adjacency: Dict[uuid.UUID, List[uuid.UUID]] = {}
    for row in edges:
        adjacency.setdefault(row.source_sector_id, []).append(row.destination_sector_id)
        if row.is_bidirectional:
            adjacency.setdefault(row.destination_sector_id, []).append(row.source_sector_id)

    tunnels = db.query(WarpTunnel).filter(WarpTunnel.status == WarpTunnelStatus.ACTIVE).all()
    for tunnel in tunnels:
        adjacency.setdefault(tunnel.origin_sector_id, []).append(tunnel.destination_sector_id)
        if tunnel.is_bidirectional:
            adjacency.setdefault(tunnel.destination_sector_id, []).append(tunnel.origin_sector_id)

    return pk_to_sector_id, adjacency


def _hop_distance(
    adjacency: Dict[uuid.UUID, List[uuid.UUID]], origin_pk: uuid.UUID, destination_pk: uuid.UUID,
) -> Optional[int]:
    if origin_pk == destination_pk:
        return 0
    visited = {origin_pk}
    frontier = [origin_pk]
    hop = 0
    while frontier:
        hop += 1
        next_frontier: List[uuid.UUID] = []
        for node in frontier:
            for neighbor in adjacency.get(node, ()):
                if neighbor == destination_pk:
                    return hop
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return None


def _all_hop_distances(
    adjacency: Dict[uuid.UUID, List[uuid.UUID]], origin_pk: uuid.UUID,
) -> Dict[uuid.UUID, int]:
    """Every reachable sector's hop distance from origin_pk, in ONE BFS
    pass — WO-SCHED-LOOP-WEDGE. generate_npc_contracts used to call
    _hop_distance() once PER CANDIDATE station considered for a given
    (origin, commodity) pair — redoing an overlapping BFS from the SAME
    origin sector over and over (up to once per candidate, per commodity,
    per origin). Since 616d122 (WarpTunnel edges connecting every region
    through the Nexus), the reachable set from any origin can span the
    WHOLE galaxy graph, so a single _hop_distance() call to a distant/
    first-tried candidate can already cost O(sectors + edges) — repeating
    that per candidate, at real galaxy scale (thousands of origins x
    thousands of candidate checks), is what wedged the scheduler's main
    loop (WO-SCHED-LOOP-WEDGE): contract generation never returned, and
    every sweep sequenced after it in the same iteration never ran.

    One full BFS per DISTINCT origin sector (the caller caches this dict,
    keyed by origin_sector_pk, so multiple origin STATIONS sharing a
    sector — and every commodity of the same origin — reuse it) turns
    every subsequent candidate reachability check into an O(1) dict
    lookup instead of a fresh traversal."""
    distances: Dict[uuid.UUID, int] = {origin_pk: 0}
    frontier = [origin_pk]
    hop = 0
    while frontier:
        hop += 1
        next_frontier: List[uuid.UUID] = []
        for node in frontier:
            for neighbor in adjacency.get(node, ()):
                if neighbor not in distances:
                    distances[neighbor] = hop
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return distances


def _active_npc_pool_count(db: Session, issuer_station_id: uuid.UUID) -> int:
    """Board size is a property of the ISSUING station (destination_
    station_id for cargo_delivery -- see contract.py's issuer_id
    docstring), not the pickup point."""
    return (
        db.query(Contract)
        .filter(
            Contract.issuer_id == issuer_station_id,
            Contract.issuer_type == ContractIssuerType.NPC,
            Contract.status.in_([ContractStatus.POSTED, ContractStatus.ACCEPTED]),
        )
        .count()
    )


def _resolve_faction_id(
    db: Session, faction_name: Optional[str], cache: Dict[str, Optional[uuid.UUID]],
) -> Optional[uuid.UUID]:
    if not faction_name:
        return None
    if faction_name in cache:
        return cache[faction_name]
    faction = db.query(Faction).filter(Faction.name == faction_name).first()
    resolved = faction.id if faction is not None else None
    cache[faction_name] = resolved
    return resolved


# --- WO-SCHED-LOOP-WEDGE: gather / compute / write split -------------------
#
# generate_npc_contracts (below) used to do everything -- read stations +
# graph, run the O(origins x commodities x candidates) reachability scan,
# and stage the INSERTs -- inside ONE open transaction. At real galaxy scale
# that scan alone ran 28+ minutes (orchestrator's live capture on heimdall:
# 99.67% CPU, one thread pegged, DB idle-in-transaction the whole time,
# waiting on nothing but Python). An open transaction spanning that long
# pins the WAL/vacuum horizon for no reason -- the compute touches no rows.
#
# Split into three phases, each usable independently:
#   1. gather_contract_generation_inputs(db, ...) -- everything the compute
#      phase needs, read in ONE short transaction. Stations are snapshotted
#      into plain, detached _StationSnapshot objects (never a live ORM
#      reference), and every per-station/per-commodity DB lookup this used
#      to do LAZILY during the scan (pool counts, faction ids, dynamic
#      prices) is done EAGERLY here instead, for every plausible candidate
#      -- so phase 2 never needs the database at all.
#   2. compute_contract_generation_batch(inputs) -- pure Python. Takes NO
#      db/Session parameter at all, so "no open transaction can span this"
#      is enforced by the function's own signature, not just caller
#      discipline. This is the (now O(1)-per-candidate-lookup, was
#      O(BFS)-per-candidate) reachability + candidate-matching scan.
#   3. write_contract_generation_batch(db, batch, ...) -- materializes the
#      computed batch into real Contract rows in a short write-only
#      transaction. Caller commits (matches every other sweep's own
#      SessionLocal-owns-commit split).
#
# generate_npc_contracts itself becomes a thin one-session convenience
# wrapper over all three -- unchanged public signature/return shape/
# behavior, so every existing caller and test is unaffected. The scheduler
# wrapper (_run_contract_generation_sync, npc_scheduler_service.py) is the
# one caller that runs the three phases against THREE SEPARATE short
# sessions instead.
# -----------------------------------------------------------------------

@dataclass
class _StationSnapshot:
    """Plain, detached copy of exactly the Station fields the generator
    needs. Extracted during the read phase so the compute phase never
    touches an ORM object or needs its owning session to still be open --
    duck-types identically whether the source was a real Station row or a
    test's SimpleNamespace fixture."""
    id: uuid.UUID
    commodities: Dict[str, Any]
    sector_uuid: Optional[uuid.UUID]
    faction_affiliation: Optional[str]
    station_class: Any
    # WO-CONTRACT-3-NPCGEN-TYPES: StationType, needed only to detect a
    # BLACK_MARKET-type destination (hazardous_transport's issuing venue,
    # contracts.md:140 -- "issued by criminal NPCs at black-market
    # terminals"). Absent on every pre-existing test fixture (SimpleNamespace
    # with no `type` attr) -> getattr defaults to None, which safely never
    # equals StationType.BLACK_MARKET -- zero behavior change for any
    # existing station fixture that doesn't opt in.
    type: Any = None


@dataclass
class GenerationInputs:
    """Output of gather_contract_generation_inputs -- everything
    compute_contract_generation_batch needs, with zero further DB access."""
    stations: List[_StationSnapshot]
    adjacency: Dict[uuid.UUID, List[uuid.UUID]]
    pool_counts: Dict[uuid.UUID, int]
    price_cache: Dict[Tuple[uuid.UUID, str], float]
    faction_cache: Dict[str, Optional[uuid.UUID]] = field(default_factory=dict)


@dataclass
class _ContractSpec:
    """Everything needed to construct one Contract row -- produced by the
    pure-Python compute phase; materialized into a real ORM row only in
    write_contract_generation_batch."""
    issuer_id: uuid.UUID
    origin_station_id: uuid.UUID
    destination_station_id: uuid.UUID
    commodity_type: str
    quantity: int
    payment: Decimal
    penalty: Decimal
    faction_id: Optional[uuid.UUID]
    deadline_hours: Decimal
    # WO-CONTRACT-3-NPCGEN-TYPES: defaults preserve every existing call
    # site/test that constructs a _ContractSpec positionally-then-cargo-
    # delivery (none do today -- all use kwargs -- but keeping these
    # optional-with-default costs nothing and avoids a forced signature
    # bump at every call site for a field only two of three types use).
    contract_type: ContractType = ContractType.CARGO_DELIVERY
    reputation_penalty: Optional[int] = None


@dataclass
class GenerationBatch:
    """Output of compute_contract_generation_batch."""
    contracts: List[_ContractSpec]
    stations_scanned: int
    blocked_by: Dict[str, int]
    # WO-CONTRACT-3-NPCGEN-TYPES: same WO-SWEEP-SILENT-SWEEPS motivation as
    # `blocked_by` -- now that a tick can post four different types (WO-
    # CONTRACT-4-BULK adds bulk_procurement), a flat `generated` count
    # alone can't distinguish "posted 5 ordinary cargo runs" from "posted
    # 5 hazardous_transport contracts" in the log.
    generated_by_type: Dict[str, int] = field(
        default_factory=lambda: {
            "cargo_delivery": 0, "express_delivery": 0, "hazardous_transport": 0, "bulk_procurement": 0,
        },
    )


def gather_contract_generation_inputs(
    db: Session, stations: Optional[List[Any]] = None,
) -> GenerationInputs:
    """Phase 1 (WO-SCHED-LOOP-WEDGE) -- read-only, meant to run in a SHORT
    transaction the caller closes right after this returns.

    Pool counts and faction ids are pre-fetched EAGERLY here for every
    plausible candidate/affiliation (reusing _active_npc_pool_count /
    _resolve_faction_id unchanged -- just called upfront instead of
    lazily during the scan), and dynamic prices are pre-fetched for every
    sell-eligible (origin, commodity) pair via TradingService (also
    unchanged) -- so compute_contract_generation_batch never needs `db`.

    `stations`, if given, overrides the full `db.query(Station).all()`
    scan (test injection point) -- real callers omit it."""
    raw_stations = stations if stations is not None else db.query(Station).all()
    snapshots = [
        _StationSnapshot(
            id=s.id,
            commodities=s.commodities or {},
            sector_uuid=getattr(s, "sector_uuid", None),
            faction_affiliation=getattr(s, "faction_affiliation", None),
            station_class=getattr(s, "station_class", None),
            type=getattr(s, "type", None),
        )
        for s in raw_stations
    ]

    if len(snapshots) < 2:
        return GenerationInputs(stations=snapshots, adjacency={}, pool_counts={}, price_cache={})

    _, adjacency = _load_directed_sector_graph(db)

    # Pool counts -- every station that flags "buys" on ANY commodity is a
    # plausible destination candidate; pre-fetch its board size now rather
    # than the first time the scan happens to encounter it as a candidate.
    buyer_candidate_ids = {
        s.id for s in snapshots
        if any(isinstance(v, dict) and v.get("buys") for v in s.commodities.values())
    }
    pool_counts: Dict[uuid.UUID, int] = {
        sid: _active_npc_pool_count(db, sid) for sid in buyer_candidate_ids
    }

    # Faction ids -- every DISTINCT affiliation actually present, once.
    faction_cache: Dict[str, Optional[uuid.UUID]] = {}
    for name in {s.faction_affiliation for s in snapshots if s.faction_affiliation}:
        _resolve_faction_id(db, name, faction_cache)

    # Dynamic prices -- every sell-eligible (origin, commodity) pair, once.
    trading_service = TradingService(db)
    price_cache: Dict[Tuple[uuid.UUID, str], float] = {}
    for snap in snapshots:
        for commodity_name, spec in snap.commodities.items():
            if isinstance(spec, dict) and spec.get("sells"):
                price_cache[(snap.id, commodity_name)] = trading_service.calculate_dynamic_price(
                    snap, commodity_name, "sell",
                )

    return GenerationInputs(
        stations=snapshots, adjacency=adjacency, pool_counts=pool_counts,
        price_cache=price_cache, faction_cache=faction_cache,
    )


def _classify_and_price_contract(
    destination: _StationSnapshot, origin_price: float, quantity: int, hops: int, deadline_hours: Decimal,
    available: int,
) -> Tuple[ContractType, Decimal, Decimal, Optional[int], int]:
    """WO-CONTRACT-3-NPCGEN-TYPES: extracted out of the compute phase's main
    scan loop so that function's own McCabe complexity stays manageable as
    this WO adds a new type-classification branch (see gameserver-ruff-
    c901-not-enforced in monk's own memory notes -- a cheap extraction is
    worth doing even though this codebase tolerates far worse elsewhere).
    Pure. Returns (contract_type, payment, penalty, reputation_penalty,
    quantity) -- see this WO's constants (EXPRESS_DEADLINE_THRESHOLD_HOURS
    / HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY) for the classification
    rules' canon citations and NO-CANON pins. WO-CONTRACT-4-BULK adds a
    third branch (bulk_procurement, keyed off `available` -- the origin's
    live stock BEFORE the per-contract quantity cap -- see BULK_
    PROCUREMENT_DEFICIT_THRESHOLD's own comment), checked after the
    black-market identity check (station identity always wins, matching
    this function's pre-existing precedence -- see test_black_market_
    destination_generates_hazardous_transport) but before the deadline-
    tightness check (bulk and express are mutually exclusive; a
    deficit-stock match is bulk regardless of how tight its drawn
    deadline happens to be). The RETURNED quantity is `quantity`
    unchanged for every branch except bulk_procurement, whose demand
    figure is NOT bounded by the thin `available` that triggered it --
    see BULK_PROCUREMENT_DEFICIT_THRESHOLD's own comment for why."""
    if destination.type == StationType.BLACK_MARKET:
        payment, penalty = compute_hazardous_transport_payment(
            Decimal(str(origin_price)), quantity, hops, deadline_hours,
        )
        return ContractType.HAZARDOUS_TRANSPORT, payment, penalty, HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY, quantity
    if available < BULK_PROCUREMENT_DEFICIT_THRESHOLD:
        bulk_quantity = MAX_CONTRACT_QUANTITY
        payment, penalty = compute_bulk_procurement_payment(
            Decimal(str(origin_price)), bulk_quantity, hops, deadline_hours,
        )
        return ContractType.BULK_PROCUREMENT, payment, penalty, None, bulk_quantity
    if deadline_hours <= EXPRESS_DEADLINE_THRESHOLD_HOURS:
        payment, penalty = compute_express_delivery_payment(
            Decimal(str(origin_price)), quantity, hops, deadline_hours,
        )
        return ContractType.EXPRESS_DELIVERY, payment, penalty, None, quantity
    payment, penalty = compute_cargo_delivery_payment(
        Decimal(str(origin_price)), quantity, hops, deadline_hours,
    )
    return ContractType.CARGO_DELIVERY, payment, penalty, None, quantity


def compute_contract_generation_batch(inputs: GenerationInputs) -> GenerationBatch:
    """Phase 2 (WO-SCHED-LOOP-WEDGE) -- pure Python, no db/Session
    parameter at all: "no open transaction spans this" is a property of
    the function's own signature, not just a caller convention.

    This is the reachability + candidate-matching scan itself -- the same
    logic generate_npc_contracts always ran, now reading exclusively from
    `inputs` (never the database). One BFS per DISTINCT origin sector
    (hop_cache below), reused across every commodity of every origin
    station docked in that sector -- see _all_hop_distances' own
    docstring for why a per-candidate BFS became the scaling bottleneck
    this WO fixes."""
    stations = inputs.stations
    if len(stations) < 2:
        return GenerationBatch(
            contracts=[], stations_scanned=len(stations),
            blocked_by={"no_buyer": 0, "unreachable": 0, "price": 0, "pool": 0},
        )

    # Local mutable copy -- in-tick reservation (a destination reached for
    # multiple commodities in the same pass must not overshoot the cap)
    # must never mutate the shared, cacheable GenerationInputs.
    pool_counts: Dict[uuid.UUID, int] = dict(inputs.pool_counts)
    hop_cache: Dict[uuid.UUID, Dict[uuid.UUID, int]] = {}
    contracts: List[_ContractSpec] = []
    blocked_by: Dict[str, int] = {"no_buyer": 0, "unreachable": 0, "price": 0, "pool": 0}
    generated_by_type: Dict[str, int] = {
        "cargo_delivery": 0, "express_delivery": 0, "hazardous_transport": 0, "bulk_procurement": 0,
    }

    for origin in stations:
        for commodity_name, spec in origin.commodities.items():
            if not isinstance(spec, dict) or not spec.get("sells"):
                continue
            available = int(spec.get("quantity", 0) or 0)
            if available < MIN_CONTRACT_QUANTITY:
                continue

            origin_price = inputs.price_cache.get((origin.id, commodity_name), 0)
            if origin_price <= 0:
                blocked_by["price"] += 1
                continue

            origin_sector_pk = origin.sector_uuid
            if origin_sector_pk is None:
                blocked_by["unreachable"] += 1
                continue

            if origin_sector_pk not in hop_cache:
                hop_cache[origin_sector_pk] = _all_hop_distances(inputs.adjacency, origin_sector_pk)
            distances_from_origin = hop_cache[origin_sector_pk]

            # Try EVERY eligible destination candidate, not just the first
            # -- a candidate that's at its pool cap or unreachable must not
            # discard the whole trade opportunity when another buyer would
            # have worked (see test_pool_cap_is_per_destination_not_global).
            destination = None
            hops = None
            any_buyer = False
            any_buyer_under_cap = False
            for candidate in stations:
                if candidate.id == origin.id:
                    continue
                candidate_spec = candidate.commodities.get(commodity_name)
                if not isinstance(candidate_spec, dict) or not candidate_spec.get("buys"):
                    continue
                any_buyer = True
                if pool_counts.get(candidate.id, 0) >= MAX_ACTIVE_NPC_CONTRACTS_PER_STATION:
                    continue
                candidate_sector_pk = candidate.sector_uuid
                if candidate_sector_pk is None:
                    continue
                any_buyer_under_cap = True
                candidate_hops = distances_from_origin.get(candidate_sector_pk)
                if candidate_hops is None:
                    continue
                destination = candidate
                hops = candidate_hops
                break
            if destination is None:
                if not any_buyer:
                    blocked_by["no_buyer"] += 1
                elif not any_buyer_under_cap:
                    blocked_by["pool"] += 1
                else:
                    blocked_by["unreachable"] += 1
                continue

            quantity = min(MAX_CONTRACT_QUANTITY, available)
            deadline_hours = Decimal(str(pick_deadline_hours()))

            # WO-CONTRACT-3-NPCGEN-TYPES: a BLACK_MARKET-type destination
            # always yields hazardous_transport (contracts.md:140 -- "issued
            # by criminal NPCs at black-market terminals" is a property of
            # WHO is issuing, not a random roll); otherwise, WO-CONTRACT-4-
            # BULK (Max-corrected direction): an origin whose live stock
            # (`available`, BEFORE the quantity cap below) is genuinely thin
            # is RECLASSIFIED bulk_procurement -- a station-short-on-stock
            # restock job, not a surplus-to-move-out one (see BULK_
            # PROCUREMENT_DEFICIT_THRESHOLD's own comment, including why the
            # returned `quantity` below is OVERRIDDEN for this branch);
            # otherwise a pair whose drawn deadline lands tight enough is
            # RECLASSIFIED express_delivery (no second, independent roll --
            # see EXPRESS_DEADLINE_THRESHOLD_HOURS' own comment); otherwise
            # plain cargo_delivery, byte-identical to this generator's
            # pre-WO behavior. See _classify_and_price_contract's own
            # docstring.
            contract_type, payment, penalty, reputation_penalty, quantity = _classify_and_price_contract(
                destination, origin_price, quantity, hops, deadline_hours, available,
            )

            faction_id = inputs.faction_cache.get(destination.faction_affiliation) \
                if destination.faction_affiliation else None

            contracts.append(_ContractSpec(
                issuer_id=destination.id,
                origin_station_id=origin.id,
                destination_station_id=destination.id,
                commodity_type=commodity_name,
                quantity=quantity,
                payment=payment,
                penalty=penalty,
                faction_id=faction_id,
                deadline_hours=deadline_hours,
                contract_type=contract_type,
                reputation_penalty=reputation_penalty,
            ))
            pool_counts[destination.id] = pool_counts.get(destination.id, 0) + 1
            generated_by_type[contract_type.value] += 1

    return GenerationBatch(
        contracts=contracts, stations_scanned=len(stations), blocked_by=blocked_by,
        generated_by_type=generated_by_type,
    )


def write_contract_generation_batch(
    db: Session, batch: GenerationBatch, now: Optional[datetime] = None,
) -> int:
    """Phase 3 (WO-SCHED-LOOP-WEDGE) -- materializes a computed batch into
    real Contract rows in a short write-only transaction. FLUSH-ONLY --
    the caller commits (matches every other sweep's own SessionLocal-
    owns-commit split; see _run_contract_generation_sync)."""
    now = now or _now()
    for spec in batch.contracts:
        db.add(Contract(
            id=uuid.uuid4(),
            issuer_type=ContractIssuerType.NPC,
            issuer_id=spec.issuer_id,
            contract_type=spec.contract_type,
            status=ContractStatus.POSTED,
            origin_station_id=spec.origin_station_id,
            destination_station_id=spec.destination_station_id,
            commodity_type=spec.commodity_type,
            quantity=spec.quantity,
            payment=spec.payment,
            penalty=spec.penalty,
            acceptance_fee_pct=Decimal("2.0"),
            escrow_amount=Decimal("0"),
            faction_id=spec.faction_id,
            # WO-CONTRACT-3-NPCGEN-TYPES: only hazardous_transport specs
            # carry a non-None reputation_penalty (see the classification
            # block in compute_contract_generation_batch) -- every other
            # type stays None, byte-identical to this column's pre-WO
            # always-unset state.
            reputation_penalty=spec.reputation_penalty,
            deadline=now + timedelta(hours=float(spec.deadline_hours)),
            posted_at=now,
            posting_stations=[spec.destination_station_id],
        ))
    db.flush()
    logger.info(
        "NPC contract generator: posted %d new contract(s) by_type=%s "
        "(scanned %d station(s), blocked_by=%s)",
        len(batch.contracts), batch.generated_by_type, batch.stations_scanned, batch.blocked_by,
    )
    return len(batch.contracts)


def generate_npc_contracts(
    db: Session,
    now: Optional[datetime] = None,
    stations: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Convenience single-session entry point -- gathers, computes, and
    writes all in ONE session/transaction (what every existing test and
    any caller besides the scheduler wrapper wants). Scan every station as
    a potential `cargo_delivery` PICKUP point (origin). For each commodity
    the origin SELLS (station.commodities[c]["sells"]) with enough live
    stock, find another station that BUYS it (station.commodities[c]
    ["buys"]) reachable via a DIRECTED warp path -- that destination
    station is the ISSUER (its own board shows the job, contract.py's
    issuer_id docstring) and its pool is what's capped. Skips zero-price
    commodities and unreachable destination pairs rather than generating a
    degenerate contract. `commodity_type` is whatever key is actually
    present on the live station registry -- NOT a hardcoded enum (a novel/
    unicode commodity name generates exactly like any other).

    `stations`, if given, overrides the full `db.query(Station).all()`
    scan (test injection point) -- real callers omit it.

    WO-SCHED-LOOP-WEDGE: the scheduler wrapper (_run_contract_generation_
    sync, npc_scheduler_service.py) does NOT call this -- it runs
    gather_contract_generation_inputs / compute_contract_generation_batch
    / write_contract_generation_batch directly, against three SEPARATE
    short sessions, so the (now O(1)-per-candidate, was O(BFS)-per-
    candidate) compute phase never pins an open transaction. This function
    is unchanged in behavior from before that split -- same reachability
    results, same generated counts, same blocked_by semantics -- it just
    runs all three phases back-to-back against the one session it's given.

    Returns a ``blocked_by`` counter dict alongside ``generated`` --
    WO-SWEEP-SILENT-SWEEPS: a scan that legitimately posts 0 looked
    identical, from the log, to one silently crashing or never running at
    all. Counts are per (origin, commodity) pair considered, not per
    candidate station -- ``price`` and ``no_buyer``/``pool``/``unreachable``
    are mutually exclusive per pair, so the four counts plus ``generated``
    sum to the number of sell-eligible (origin, commodity) pairs scanned."""
    inputs = gather_contract_generation_inputs(db, stations=stations)
    batch = compute_contract_generation_batch(inputs)
    generated = write_contract_generation_batch(db, batch, now=now)
    return {
        "generated": generated, "stations_scanned": batch.stations_scanned,
        "blocked_by": batch.blocked_by, "generated_by_type": batch.generated_by_type,
    }
