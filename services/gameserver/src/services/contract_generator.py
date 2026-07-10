"""
NPC contract generator -- WO-ECON-CONTRACT-1-KERNEL lane 3, contracts.md's
own build order step 2 ("NPC `cargo_delivery` generator (simplest type) --
proves the lifecycle end-to-end", :426). Generates `cargo_delivery`
contracts ONLY; the other six `contract_type` values in the schema are a
later build step (:429) and this generator never produces them.

SYNC Session -- `generate_npc_contracts(db, ...)` is the pure, testable
core; the npc_scheduler_service.py wrapper owns its own SessionLocal +
commit, matching every other scheduler sweep in that module (see
`_run_price_alert_sweep_sync` for the precedent this mirrors).
"""
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from src.models.contract import Contract, ContractIssuerType, ContractStatus, ContractType
from src.models.faction import Faction
from src.models.sector import Sector, sector_warps
from src.models.station import Station
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
CARGO_DELIVERY_TYPE_MULTIPLIER = Decimal("1.0")
PENALTY_MULTIPLIER = Decimal("1.0")  # contracts.md:40 -- default 1.0x payment for cargo


def _now() -> datetime:
    return datetime.now(UTC)


def pick_deadline_hours() -> float:
    """Isolated so tests can monkeypatch it for determinism."""
    return random.uniform(MIN_DEADLINE_HOURS, MAX_DEADLINE_HOURS)  # noqa: S311 -- gameplay timing, not crypto


def compute_cargo_delivery_payment(
    unit_price: Decimal, quantity: int, hops: int, deadline_hours: Decimal,
) -> Tuple[Decimal, Decimal]:
    """Pure -- no DB, no side effects. `payment` is monotone-increasing in
    `quantity` by construction (quantity is a positive linear factor of
    `commodity_value`, and every other factor is a positive multiplier).
    Returns (payment, penalty), both quantized to cents."""
    commodity_value = unit_price * Decimal(quantity)
    distance_factor = Decimal("1.0") + DISTANCE_FACTOR_PER_HOP * Decimal(hops)
    tightness = URGENCY_STANDARD_HOURS - deadline_hours
    urgency_factor = Decimal("1.0") + max(Decimal("0"), tightness) * URGENCY_PER_HOUR
    urgency_factor = min(urgency_factor, URGENCY_FACTOR_CEILING)
    payment = (
        BASE_RATE * commodity_value * distance_factor * urgency_factor * CARGO_DELIVERY_TYPE_MULTIPLIER
    ).quantize(Decimal("0.01"))
    penalty = (payment * PENALTY_MULTIPLIER).quantize(Decimal("0.01"))
    return payment, penalty


# --- sector-graph helpers (private to this service -- mirrors escape_pod_
# service.py / slipdrive_service.py's own per-service-graph-helper
# convention). DIRECTED (unlike the stranding-recovery trio's undirected
# BFS) -- this hop count represents a REAL delivery run a player must fly,
# which must respect warp directionality, not an out-of-band teleport. ---

def _load_directed_sector_graph(db: Session) -> Tuple[Dict[uuid.UUID, int], Dict[uuid.UUID, List[uuid.UUID]]]:
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


def generate_npc_contracts(
    db: Session,
    now: Optional[datetime] = None,
    stations: Optional[List[Any]] = None,
) -> Dict[str, int]:
    """Scan every station as a potential `cargo_delivery` PICKUP point
    (origin). For each commodity the origin SELLS
    (station.commodities[c]["sells"]) with enough live stock, find another
    station that BUYS it (station.commodities[c]["buys"]) reachable via a
    DIRECTED warp path -- that destination station is the ISSUER (its own
    board shows the job, contract.py's issuer_id docstring) and its pool
    is what's capped. Skips zero-price commodities and unreachable
    destination pairs rather than generating a degenerate contract.
    `commodity_type` is whatever key is actually present on the live
    station registry -- NOT a hardcoded enum (a novel/unicode commodity
    name generates exactly like any other). FLUSH-ONLY -- the scheduler
    wrapper commits.

    `stations`, if given, overrides the full `db.query(Station).all()`
    scan (test injection point) -- real callers omit it."""
    now = now or _now()
    all_stations = stations if stations is not None else db.query(Station).all()
    if len(all_stations) < 2:
        return {"generated": 0, "stations_scanned": len(all_stations)}

    pk_to_sector_id, adjacency = _load_directed_sector_graph(db)
    trading_service = TradingService(db)
    faction_cache: Dict[str, Optional[uuid.UUID]] = {}
    # In-tick pool tracking, keyed by issuer (destination) station -- seeded
    # lazily from the DB on first encounter, then updated in-memory so a
    # station reached as a destination for multiple commodities in the same
    # tick doesn't re-query and doesn't overshoot the cap.
    pool_counts: Dict[uuid.UUID, int] = {}
    generated = 0

    for origin in all_stations:
        origin_commodities = origin.commodities or {}
        for commodity_name, spec in origin_commodities.items():
            if not isinstance(spec, dict) or not spec.get("sells"):
                continue
            available = int(spec.get("quantity", 0) or 0)
            if available < MIN_CONTRACT_QUANTITY:
                continue

            origin_price = trading_service.calculate_dynamic_price(origin, commodity_name, "sell")
            if origin_price <= 0:
                continue

            # Try EVERY eligible destination candidate, not just the first
            # -- a candidate that's at its pool cap or unreachable must not
            # discard the whole trade opportunity when another buyer would
            # have worked (see test_pool_cap_is_per_destination_not_global).
            origin_sector_pk = getattr(origin, "sector_uuid", None)
            if origin_sector_pk is None:
                continue

            destination = None
            hops = None
            for candidate in all_stations:
                if candidate.id == origin.id:
                    continue
                candidate_spec = (candidate.commodities or {}).get(commodity_name)
                if not isinstance(candidate_spec, dict) or not candidate_spec.get("buys"):
                    continue
                if candidate.id not in pool_counts:
                    pool_counts[candidate.id] = _active_npc_pool_count(db, candidate.id)
                if pool_counts[candidate.id] >= MAX_ACTIVE_NPC_CONTRACTS_PER_STATION:
                    continue
                candidate_sector_pk = getattr(candidate, "sector_uuid", None)
                if candidate_sector_pk is None:
                    continue
                candidate_hops = _hop_distance(adjacency, origin_sector_pk, candidate_sector_pk)
                if candidate_hops is None:
                    continue
                destination = candidate
                hops = candidate_hops
                break
            if destination is None:
                continue

            quantity = min(MAX_CONTRACT_QUANTITY, available)
            deadline_hours = Decimal(str(pick_deadline_hours()))
            payment, penalty = compute_cargo_delivery_payment(
                Decimal(str(origin_price)), quantity, hops, deadline_hours,
            )
            faction_id = _resolve_faction_id(db, getattr(destination, "faction_affiliation", None), faction_cache)

            contract = Contract(
                id=uuid.uuid4(),
                issuer_type=ContractIssuerType.NPC,
                issuer_id=destination.id,
                contract_type=ContractType.CARGO_DELIVERY,
                status=ContractStatus.POSTED,
                origin_station_id=origin.id,
                destination_station_id=destination.id,
                commodity_type=commodity_name,
                quantity=quantity,
                payment=payment,
                penalty=penalty,
                acceptance_fee_pct=Decimal("2.0"),
                escrow_amount=Decimal("0"),
                faction_id=faction_id,
                deadline=now + timedelta(hours=float(deadline_hours)),
                posted_at=now,
                posting_stations=[destination.id],
            )
            db.add(contract)
            generated += 1
            pool_counts[destination.id] += 1

    db.flush()
    logger.info("NPC contract generator: posted %d new cargo_delivery contract(s)", generated)
    return {"generated": generated, "stations_scanned": len(all_stations)}
