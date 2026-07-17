"""WO-ECON-CONTRACT-1-KERNEL lane 5 -- contract_generator.py.

DB-free: real SQLAlchemy filter-clause interpretation for Contract/Faction
queries (mirrors test_contract_service.py's interpreter); Sector /
sector_warps reads are dispatched by identity match on the query's head
entity, matching escape_pod_service.py / slipdrive_service.py's own test
convention. Stations are injected directly via `stations=` (the
generator's documented test-injection point) -- no Station query needed.
"""
from __future__ import annotations

import inspect
import operator
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractIssuerType, ContractStatus, ContractType
from src.models.faction import Faction
from src.models.sector import Sector, sector_warps
from src.models.station import StationClass, StationType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services import contract_generator
from src.services.contract_generator import (
    compute_bulk_procurement_payment,
    compute_cargo_delivery_payment,
    compute_express_delivery_payment,
    compute_hazardous_transport_payment,
)


def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None

    def count(self) -> int:
        return sum(1 for row in self._rows if all(_match(row, c) for c in self._criteria))

    def all(self) -> List[Any]:
        # No pre-existing query in this file chained .filter() before
        # .all() (the sector_warps/Sector reads used here are unfiltered),
        # so this used to ignore self._criteria entirely -- silently
        # correct only because it was never exercised. The new WarpTunnel
        # query (WO-SWEEP-SILENT-SWEEPS) is the first .filter().all() in
        # this file's call graph and needs criteria genuinely applied.
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]


class _FakeSession:
    def __init__(
        self, *, sectors: Optional[List[Any]] = None, edges: Optional[List[Any]] = None,
        contracts: Optional[List[Any]] = None, factions: Optional[List[Any]] = None,
        tunnels: Optional[List[Any]] = None,
    ) -> None:
        self.sectors = sectors or []
        self.edges = edges or []
        self.contracts = contracts or []
        self.factions = factions or []
        self.tunnels = tunnels or []
        self.added: List[Any] = []
        self.flush_calls = 0

    def query(self, *entities: Any) -> _FakeQuery:
        head = entities[0]
        if head is Sector.id:
            return _FakeQuery(self.sectors)
        if head is sector_warps.c.source_sector_id:
            return _FakeQuery(self.edges)
        if head is Contract:
            return _FakeQuery(self.contracts)
        if head is Faction:
            return _FakeQuery(self.factions)
        if head is WarpTunnel:
            return _FakeQuery(self.tunnels)
        raise AssertionError(f"unexpected query for {entities!r}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.contracts.append(obj)

    def flush(self) -> None:
        self.flush_calls += 1


# --- fixtures ------------------------------------------------------------ #

def _sector(sector_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def _edge(a: SimpleNamespace, b: SimpleNamespace, *, bidirectional: bool = False) -> SimpleNamespace:
    return SimpleNamespace(source_sector_id=a.id, destination_sector_id=b.id, is_bidirectional=bidirectional)


def _tunnel(
    a: SimpleNamespace, b: SimpleNamespace, *,
    bidirectional: bool = False, status: WarpTunnelStatus = WarpTunnelStatus.ACTIVE,
) -> SimpleNamespace:
    """WarpTunnel row shape (WO-SWEEP-SILENT-SWEEPS) -- the inter-region
    connection bang_import_service._add_nexus_warp actually writes; a
    sector_warps-only graph never includes it (see
    test_cross_region_pair_reachable_only_via_warp_tunnel below)."""
    return SimpleNamespace(
        origin_sector_id=a.id, destination_sector_id=b.id,
        is_bidirectional=bidirectional, status=status,
    )


def _station(
    *, sector: SimpleNamespace, commodities: dict, faction_affiliation: Optional[str] = None,
    type: Optional[StationType] = None,  # noqa: A002 -- matches Station.type's own column name
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), sector_uuid=sector.id, commodities=commodities,
        faction_affiliation=faction_affiliation,
        station_class=StationClass.CLASS_3,  # neutral -- no 8/9/11 premium
        # WO-CONTRACT-3-NPCGEN-TYPES: `type=None` (the default) is NOT the
        # same as omitting the attribute -- but contract_generator.py reads
        # it via `getattr(s, "type", None)`, so both read back as None and
        # every pre-WO test (which never passed `type=`) is unaffected.
        type=type,
    )


def _sells(quantity: int, base_price: int = 20, capacity: int = 200) -> dict:
    return {"sells": True, "quantity": quantity, "capacity": capacity, "base_price": base_price}


def _buys() -> dict:
    return {"buys": True}


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _one_hop_pair(commodity: str = "ore", origin_qty: int = 100):
    sec_a, sec_b = _sector(1), _sector(2)
    origin = _station(sector=sec_a, commodities={commodity: _sells(origin_qty)})
    destination = _station(sector=sec_b, commodities={commodity: _buys()})
    db = _FakeSession(sectors=[sec_a, sec_b], edges=[_edge(sec_a, sec_b)])
    return db, origin, destination


@pytest.mark.unit
class TestGenerateNpcContracts:
    def test_generates_one_contract_for_simple_two_station_pair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])

        assert result["generated"] == 1
        assert len(db.added) == 1
        c = db.added[0]
        assert c.issuer_type == ContractIssuerType.NPC
        assert c.issuer_id == destination.id  # issuer = destination, see contract.py docstring
        assert c.origin_station_id == origin.id
        assert c.destination_station_id == destination.id
        assert c.commodity_type == "ore"
        assert c.quantity == 100
        assert c.payment > 0
        assert c.posting_stations == [destination.id]
        assert c.deadline == _NOW + timedelta(hours=3.0)
        assert db.flush_calls == 1

    def test_faction_resolved_from_destination_not_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        origin.faction_affiliation = "Fringe Alliance"
        destination.faction_affiliation = "Terran Federation"
        federation_id = uuid.uuid4()
        db.factions = [SimpleNamespace(id=federation_id, name="Terran Federation")]

        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])

        assert db.added[0].faction_id == federation_id

    def test_single_station_is_a_no_op(self) -> None:
        db, origin, _destination = _one_hop_pair()
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin])
        assert result == {
            "generated": 0, "stations_scanned": 1,
            "blocked_by": {"no_buyer": 0, "unreachable": 0, "price": 0, "pool": 0},
            "generated_by_type": {
                "cargo_delivery": 0, "express_delivery": 0, "hazardous_transport": 0, "bulk_procurement": 0,
            },
        }

    def test_skips_zero_price_commodity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """calculate_dynamic_price's own band-clamp floors any KNOWN
        commodity at COMMODITY_PRICE_RANGES min (never 0) -- the only real
        path to a non-positive price is TradingService's own "commodity
        not found" branch, which this generator's call pattern can never
        hit (it only ever prices a commodity_name it just read off the
        same station). The `origin_price <= 0` guard is still real
        defensive code for that method's documented contract -- exercised
        directly here rather than fought through the pricing floor."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        monkeypatch.setattr(
            "src.services.trading_service.TradingService.calculate_dynamic_price",
            lambda self, station, commodity, transaction_type: 0,
        )

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 0

    def test_skips_unreachable_destination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()})
        db = _FakeSession(sectors=[sec_a, sec_b], edges=[])  # no path at all

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 0

    def test_skips_below_minimum_stock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(origin_qty=contract_generator.MIN_CONTRACT_QUANTITY - 1)
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 0

    def test_quantity_capped_at_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(origin_qty=contract_generator.MAX_CONTRACT_QUANTITY + 500)
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].quantity == contract_generator.MAX_CONTRACT_QUANTITY

    def test_novel_registry_commodity_generates_normally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not one of the 'canonical seven' -- a live-registry-only
        commodity must generate exactly like any other."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(commodity="xenocrystal_dust")
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].commodity_type == "xenocrystal_dust"

    def test_unicode_commodity_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(commodity="水晶素材")  # 水晶素材
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].commodity_type == "水晶素材"

    def test_pool_cap_skips_further_generation_for_that_destination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        # Seed the destination's board already at cap with active NPC contracts.
        existing = [
            SimpleNamespace(
                id=uuid.uuid4(), issuer_id=destination.id, issuer_type=ContractIssuerType.NPC,
                status=ContractStatus.POSTED,
            )
            for _ in range(contract_generator.MAX_ACTIVE_NPC_CONTRACTS_PER_STATION)
        ]
        db.contracts = list(existing)

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 0
        assert len(db.added) == 0

    def test_pool_cap_is_per_destination_not_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b, sec_c = _sector(1), _sector(2), _sector(3)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        capped_destination = _station(sector=sec_b, commodities={"ore": _buys()})
        open_destination = _station(sector=sec_c, commodities={"ore": _buys()})
        db = _FakeSession(
            sectors=[sec_a, sec_b, sec_c],
            edges=[_edge(sec_a, sec_b), _edge(sec_a, sec_c)],
        )
        db.contracts = [
            SimpleNamespace(
                id=uuid.uuid4(), issuer_id=capped_destination.id, issuer_type=ContractIssuerType.NPC,
                status=ContractStatus.POSTED,
            )
            for _ in range(contract_generator.MAX_ACTIVE_NPC_CONTRACTS_PER_STATION)
        ]

        result = contract_generator.generate_npc_contracts(
            db, now=_NOW, stations=[origin, capped_destination, open_destination],
        )
        # Exactly one contract generated -- for the OPEN destination only
        # (only one origin commodity to source from, so at most one match
        # per destination candidate; the `next(...)` destination-picker
        # returns the FIRST eligible station in list order, which is the
        # capped one -- proving the cap genuinely reroutes generation
        # rather than merely counting).
        assert result["generated"] == 1
        assert db.added[0].destination_station_id == open_destination.id

    def test_cross_region_pair_reachable_only_via_warp_tunnel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WO-SWEEP-SILENT-SWEEPS root-cause regression: bang_import_
        service._add_nexus_warp wires every spoke region to the Nexus with
        a WarpTunnel row, never a sector_warps row -- that's the ONLY
        inter-region edge that exists. No sector_warps edge here at all
        (mirrors two real regions with zero in-region overlap); the pair
        is reachable ONLY through the WarpTunnel. Fails pre-fix (with
        _load_directed_sector_graph reading sector_warps alone, this pair
        is unreachable and generates nothing)."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()})
        db = _FakeSession(
            sectors=[sec_a, sec_b], edges=[],  # no sector_warps edge anywhere
            tunnels=[_tunnel(sec_a, sec_b)],
        )

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 1
        assert db.added[0].destination_station_id == destination.id

    def test_bidirectional_warp_tunnel_reaches_both_ways(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b = _sector(1), _sector(2)
        # Tunnel direction is B -> A; origin is A. Only reachable if the
        # bidirectional flag adds the reverse edge too.
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()})
        db = _FakeSession(
            sectors=[sec_a, sec_b], edges=[],
            tunnels=[_tunnel(sec_b, sec_a, bidirectional=True)],
        )

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 1

    def test_inactive_warp_tunnel_is_not_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()})
        db = _FakeSession(
            sectors=[sec_a, sec_b], edges=[],
            tunnels=[_tunnel(sec_a, sec_b, status=WarpTunnelStatus.COLLAPSED)],
        )

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated"] == 0
        assert result["blocked_by"]["unreachable"] == 1


@pytest.mark.unit
class TestBlockedByCounters:
    """WO-SWEEP-SILENT-SWEEPS: generate_npc_contracts' blocked_by dict is
    the thing that turns a silent legitimate-zero into a diagnosable one --
    one test per bucket, each isolating exactly one blocking reason."""

    def test_price_bucket_counts_non_positive_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        monkeypatch.setattr(
            "src.services.trading_service.TradingService.calculate_dynamic_price",
            lambda self, station, commodity, transaction_type: 0,
        )
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["blocked_by"] == {"no_buyer": 0, "unreachable": 0, "price": 1, "pool": 0}

    def test_no_buyer_bucket_counts_pairs_with_zero_buy_flagged_stations(self) -> None:
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        # A second station exists, but never flags "ore" as buys -- no
        # candidate is even a buyer, distinct from "a buyer exists but is
        # unreachable/pool-capped".
        other = _station(sector=sec_b, commodities={"fuel": _buys()})
        db = _FakeSession(sectors=[sec_a, sec_b], edges=[_edge(sec_a, sec_b)])

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, other])
        assert result["blocked_by"] == {"no_buyer": 1, "unreachable": 0, "price": 0, "pool": 0}

    def test_unreachable_bucket_counts_pairs_with_a_buyer_but_no_path(self) -> None:
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()})
        db = _FakeSession(sectors=[sec_a, sec_b], edges=[])  # buyer exists, no path at all

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["blocked_by"] == {"no_buyer": 0, "unreachable": 1, "price": 0, "pool": 0}

    def test_pool_bucket_counts_pairs_where_every_buyer_is_at_cap(self) -> None:
        db, origin, destination = _one_hop_pair()
        db.contracts = [
            SimpleNamespace(
                id=uuid.uuid4(), issuer_id=destination.id, issuer_type=ContractIssuerType.NPC,
                status=ContractStatus.POSTED,
            )
            for _ in range(contract_generator.MAX_ACTIVE_NPC_CONTRACTS_PER_STATION)
        ]

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["blocked_by"] == {"no_buyer": 0, "unreachable": 0, "price": 0, "pool": 1}

    def test_buckets_and_generated_sum_to_pairs_scanned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """generated + every blocked_by bucket must account for every
        sell-eligible (origin, commodity) pair scanned -- no pair silently
        falls through uncounted."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        total = result["generated"] + sum(result["blocked_by"].values())
        assert total == 1  # exactly one (origin, commodity) pair existed


@pytest.mark.unit
class TestComputeCargoDeliveryPayment:
    def test_monotone_in_quantity(self) -> None:
        p10, _ = compute_cargo_delivery_payment(Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"))
        p20, _ = compute_cargo_delivery_payment(Decimal("20"), 20, hops=1, deadline_hours=Decimal("4.0"))
        p30, _ = compute_cargo_delivery_payment(Decimal("20"), 30, hops=1, deadline_hours=Decimal("4.0"))
        assert p10 < p20 < p30
        # Linear in quantity (all other factors held fixed) -- exact double.
        assert p20 == p10 * 2

    def test_distance_factor_matches_worked_example(self) -> None:
        """contracts.md:328-340 worked example: 8 hops -> distance_factor
        1.40 -- verbatim from the doc's own formula."""
        p0, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=0, deadline_hours=Decimal("4.0"))
        p8, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=8, deadline_hours=Decimal("4.0"))
        assert p8 / p0 == Decimal("1.40")

    def test_urgency_factor_matches_worked_example_anchor(self) -> None:
        """90-minute deadline -> urgency_factor 1.10 anchor (contracts.md's
        own worked number), reproduced by this kernel's NO-CANON
        coefficients: (4.0 - 1.5) x 0.04 = 0.10."""
        p_urgent, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=0, deadline_hours=Decimal("1.5"))
        p_standard, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=0, deadline_hours=Decimal("4.0"))
        assert p_urgent / p_standard == Decimal("1.10")

    def test_urgency_factor_ceiling_caps_extreme_tightness(self) -> None:
        """The generator itself never passes deadline_hours below
        MIN_DEADLINE_HOURS (1.0), so at the tightest REAL deadline
        urgency_factor only reaches 1.12 -- nowhere near the 2.0 ceiling.
        The ceiling is a defensive bound on this PURE function's contract
        for any future caller, not something current generation reaches;
        exercise it directly with a hypothetical extreme value."""
        p_baseline, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=0, deadline_hours=Decimal("4.0"))
        p_extreme, _ = compute_cargo_delivery_payment(Decimal("100"), 10, hops=0, deadline_hours=Decimal("-100"))
        assert p_extreme / p_baseline == contract_generator.URGENCY_FACTOR_CEILING

    def test_realistic_min_deadline_never_reaches_the_ceiling(self) -> None:
        payment, _ = compute_cargo_delivery_payment(
            Decimal("100"), 10, hops=0, deadline_hours=Decimal(str(contract_generator.MIN_DEADLINE_HOURS)),
        )
        assert payment == Decimal("1120.00")  # 100*10 x (1.0 + (4.0-1.0)*0.04) = 1000 x 1.12

    def test_penalty_equals_payment_at_default_multiplier(self) -> None:
        payment, penalty = compute_cargo_delivery_payment(Decimal("50"), 10, hops=2, deadline_hours=Decimal("4.0"))
        assert penalty == payment  # contracts.md:40 -- default 1.0x payment for cargo

    def test_zero_hops_zero_tightness_is_base_commodity_value(self) -> None:
        payment, _ = compute_cargo_delivery_payment(Decimal("20"), 10, hops=0, deadline_hours=Decimal("4.0"))
        assert payment == Decimal("200.00")  # 20 x 10 x 1.0 x 1.0 x 1.0


@pytest.mark.unit
class TestComputeExpressDeliveryPayment:
    """WO-CONTRACT-3-NPCGEN-TYPES. Same shared formula as cargo_delivery
    (_compute_typed_contract_payment) -- these tests pin the DIFFERENT
    type_multiplier/penalty_multiplier this type applies on top of it."""

    def test_pays_type_multiplier_times_the_cargo_equivalent(self) -> None:
        cargo_payment, _ = compute_cargo_delivery_payment(Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"))
        express_payment, _ = compute_express_delivery_payment(
            Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"),
        )
        # contracts.md:319 -- "roughly 1.5-2.0x their non-express
        # equivalents"; this kernel pins the midpoint (1.75x).
        assert express_payment / cargo_payment == contract_generator.EXPRESS_DELIVERY_TYPE_MULTIPLIER
        assert Decimal("1.5") <= express_payment / cargo_payment <= Decimal("2.0")

    def test_penalty_stricter_than_payment_itself(self) -> None:
        """contracts.md:136 -- "Express contracts use a stricter penalty on
        failure": penalty > payment (unlike cargo_delivery, where they're
        equal at the default 1.0x multiplier)."""
        payment, penalty = compute_express_delivery_payment(Decimal("50"), 10, hops=2, deadline_hours=Decimal("4.0"))
        assert penalty > payment
        assert penalty / payment == contract_generator.EXPRESS_PENALTY_MULTIPLIER


@pytest.mark.unit
class TestComputeHazardousTransportPayment:
    """WO-CONTRACT-3-NPCGEN-TYPES (partial -- generation only)."""

    def test_pays_significantly_more_than_cargo_equivalent(self) -> None:
        cargo_payment, _ = compute_cargo_delivery_payment(Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"))
        hazardous_payment, _ = compute_hazardous_transport_payment(
            Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"),
        )
        # contracts.md:420 -- "pay 2-4x standard rates"; this kernel pins
        # the midpoint (3.0x).
        assert hazardous_payment / cargo_payment == contract_generator.HAZARDOUS_TRANSPORT_TYPE_MULTIPLIER
        assert Decimal("2.0") <= hazardous_payment / cargo_payment <= Decimal("4.0")

    def test_penalty_multiplier_matches_cargo_default(self) -> None:
        """No canon number for a distinct hazardous FAILURE penalty (unlike
        express's explicit "stricter" language) -- reuses the plain 1.0x
        cargo_delivery default."""
        payment, penalty = compute_hazardous_transport_payment(
            Decimal("50"), 10, hops=2, deadline_hours=Decimal("4.0"),
        )
        assert penalty == payment


@pytest.mark.unit
class TestComputeBulkProcurementPayment:
    """WO-CONTRACT-4-BULK. Same shared formula as cargo_delivery -- both
    multipliers pinned at 1.0 (no canon payment premium exists for bulk,
    see BULK_PROCUREMENT_TYPE_MULTIPLIER's own comment) -- penalty ==
    payment exactly, which the WO-4 degenerate-case walk-away penalty (a
    bulk contract with no locker deposits, contract_service.py) reads
    directly off the static `penalty` column and requires."""

    def test_matches_cargo_delivery_payment_exactly(self) -> None:
        cargo_payment, cargo_penalty = compute_cargo_delivery_payment(
            Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"),
        )
        bulk_payment, bulk_penalty = compute_bulk_procurement_payment(
            Decimal("20"), 10, hops=1, deadline_hours=Decimal("4.0"),
        )
        assert bulk_payment == cargo_payment
        assert bulk_penalty == cargo_penalty

    def test_penalty_equals_payment(self) -> None:
        payment, penalty = compute_bulk_procurement_payment(
            Decimal("50"), 10, hops=2, deadline_hours=Decimal("4.0"),
        )
        assert penalty == payment  # hard requirement -- see this class's own docstring


@pytest.mark.unit
class TestTypeClassification:
    """WO-CONTRACT-3-NPCGEN-TYPES: which of the three generated types a
    matched (origin, commodity, destination) pair becomes."""

    def test_black_market_destination_generates_hazardous_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(100)})
        destination = _station(
            sector=sec_b, commodities={"ore": _buys()}, type=StationType.BLACK_MARKET,
        )
        db = _FakeSession(sectors=[sec_a, sec_b], edges=[_edge(sec_a, sec_b)])

        # Even with a deliberately NON-tight deadline (5.0h, above the
        # express threshold) -- proves black-market classification takes
        # priority over the deadline-tightness check, not the other way
        # around.
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 5.0)
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])

        assert result["generated"] == 1
        assert result["generated_by_type"] == {
            "cargo_delivery": 0, "express_delivery": 0, "hazardous_transport": 1, "bulk_procurement": 0,
        }
        c = db.added[0]
        assert c.contract_type == ContractType.HAZARDOUS_TRANSPORT
        assert c.reputation_penalty == contract_generator.HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY
        assert c.reputation_penalty < 0  # a penalty, stored as a negative delta

    def test_non_black_market_destination_never_generates_hazardous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 5.0)
        db, origin, destination = _one_hop_pair()  # default _station() -> type=None
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].contract_type == ContractType.CARGO_DELIVERY
        assert db.added[0].reputation_penalty is None

    def test_tight_deadline_reclassifies_as_express_delivery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 1.5)  # <= 2.0h threshold
        db, origin, destination = _one_hop_pair()
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated_by_type"] == {
            "cargo_delivery": 0, "express_delivery": 1, "hazardous_transport": 0, "bulk_procurement": 0,
        }
        c = db.added[0]
        assert c.contract_type == ContractType.EXPRESS_DELIVERY
        assert c.reputation_penalty is None  # express carries no reputation column write
        assert c.deadline == _NOW + timedelta(hours=1.5)  # SAME drawn deadline, not a second roll

    def test_standard_deadline_stays_cargo_delivery_byte_identical_to_pre_wo(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every pre-WO test in this file relies on this exact behavior
        (monkeypatching pick_deadline_hours to 3.0, always > the 2.0h
        express threshold) -- this test makes the regression-safety
        property explicit rather than merely incidental."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()
        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert result["generated_by_type"] == {
            "cargo_delivery": 1, "express_delivery": 0, "hazardous_transport": 0, "bulk_procurement": 0,
        }
        assert db.added[0].contract_type == ContractType.CARGO_DELIVERY

    def test_exactly_at_express_threshold_is_express_not_cargo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Boundary pin: `<=`, not `<` -- a deadline landing EXACTLY on
        EXPRESS_DEADLINE_THRESHOLD_HOURS still reclassifies."""
        monkeypatch.setattr(
            contract_generator, "pick_deadline_hours",
            lambda: float(contract_generator.EXPRESS_DEADLINE_THRESHOLD_HOURS),
        )
        db, origin, destination = _one_hop_pair()
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].contract_type == ContractType.EXPRESS_DELIVERY

    def test_stock_deficit_reclassifies_as_bulk_procurement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WO-CONTRACT-4-BULK (Max-corrected direction, 2026-07-17): an
        origin genuinely SHORT on live stock -- not one with a surplus --
        reclassifies. See BULK_PROCUREMENT_DEFICIT_THRESHOLD's own comment.
        MIN_CONTRACT_QUANTITY itself is the lowest reachable `available`
        (the scan loop's own earlier gate discards anything below it), so
        it's also the clearest in-band deficit value to exercise here.

        The WO-4 hard requirement: penalty == payment exactly (the
        degenerate-case walk-away penalty reads the static column
        directly). The quantity catch: the generated `quantity` must be
        the demand figure (MAX_CONTRACT_QUANTITY), NOT the thin
        `available` that triggered the branch -- a deficit-capped bulk
        quantity would be semantically broken (a "bulk" job for a
        handful of units)."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(origin_qty=contract_generator.MIN_CONTRACT_QUANTITY)

        result = contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])

        assert result["generated_by_type"] == {
            "cargo_delivery": 0, "express_delivery": 0, "hazardous_transport": 0, "bulk_procurement": 1,
        }
        c = db.added[0]
        assert c.contract_type == ContractType.BULK_PROCUREMENT
        assert c.penalty == c.payment  # hard requirement -- WO-4 degenerate-case walk-away penalty
        # demand figure, NOT the thin origin_qty that triggered the branch
        assert c.quantity == contract_generator.MAX_CONTRACT_QUANTITY
        assert c.reputation_penalty is None

    def test_stock_exactly_at_deficit_threshold_is_not_bulk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Boundary pin: `<`, not `<=` -- stock landing EXACTLY at
        BULK_PROCUREMENT_DEFICIT_THRESHOLD stays plain cargo_delivery."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair(origin_qty=contract_generator.BULK_PROCUREMENT_DEFICIT_THRESHOLD)
        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].contract_type == ContractType.CARGO_DELIVERY

    def test_black_market_precedence_over_bulk_stock_deficit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Station-identity classification (hazardous_transport) still
        takes priority over a deficit-stock match -- mirrors this class's
        own black-market-vs-deadline precedence test above."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        sec_a, sec_b = _sector(1), _sector(2)
        origin = _station(sector=sec_a, commodities={"ore": _sells(contract_generator.MIN_CONTRACT_QUANTITY)})
        destination = _station(sector=sec_b, commodities={"ore": _buys()}, type=StationType.BLACK_MARKET)
        db = _FakeSession(sectors=[sec_a, sec_b], edges=[_edge(sec_a, sec_b)])

        contract_generator.generate_npc_contracts(db, now=_NOW, stations=[origin, destination])
        assert db.added[0].contract_type == ContractType.HAZARDOUS_TRANSPORT


@pytest.mark.unit
class TestGatherComputeWriteSplit:
    """WO-SCHED-LOOP-WEDGE: the orchestrator's live capture on heimdall
    found generate_npc_contracts idle-in-transaction for 28+ minutes, one
    thread pegged at 99.67% CPU running pure-Python reachability compute
    the whole time. Split into gather / compute / write so the compute
    phase structurally cannot hold an open transaction (it takes no db
    parameter at all) and structurally cannot re-run an overlapping BFS
    per candidate (one full traversal per DISTINCT origin sector, cached)."""

    def test_compute_phase_takes_no_db_parameter(self) -> None:
        """Structural pin: compute_contract_generation_batch's signature
        has no db/Session argument -- "no open transaction can span the
        compute" is enforced by the function's own type, not just caller
        discipline."""
        params = list(inspect.signature(contract_generator.compute_contract_generation_batch).parameters)
        assert params == ["inputs"]

    def test_hop_distances_computed_once_per_origin_sector_not_per_candidate(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Falsifier for the scaling fix: _all_hop_distances must be
        called AT MOST once per DISTINCT origin sector, never once per
        candidate considered -- the exact bottleneck the orchestrator's
        live capture found. 3 origins sharing ONE sector, each selling a
        different commodity to a common destination, is 3 (origin,
        commodity) pairs but only ONE distinct origin sector -- pre-fix
        (a fresh BFS per candidate) this would have run the traversal
        3 times; the cache collapses it to 1."""
        call_count = 0
        real_all_hop_distances = contract_generator._all_hop_distances

        def _spy(adjacency: Any, origin_pk: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return real_all_hop_distances(adjacency, origin_pk)

        monkeypatch.setattr(contract_generator, "_all_hop_distances", _spy)

        sec_origin, sec_dest = _sector(1), _sector(2)
        db = _FakeSession(sectors=[sec_origin, sec_dest], edges=[_edge(sec_origin, sec_dest)])
        origins = [
            _station(sector=sec_origin, commodities={f"ore{i}": _sells(100)})
            for i in range(3)
        ]
        destination = _station(sector=sec_dest, commodities={f"ore{i}": _buys() for i in range(3)})

        inputs = contract_generator.gather_contract_generation_inputs(db, stations=[*origins, destination])
        batch = contract_generator.compute_contract_generation_batch(inputs)

        assert call_count == 1, f"expected exactly 1 BFS for 1 distinct origin sector, got {call_count}"
        assert len(batch.contracts) == 3  # all 3 pairs still matched correctly

    def test_three_phases_compose_to_the_same_result_as_the_single_call_wrapper(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate_npc_contracts (the preserved single-session API every
        other test in this file exercises) must produce the IDENTICAL
        result whether called directly or via its own three phases run
        by hand -- proving the split is a pure refactor, not a behavior
        change."""
        monkeypatch.setattr(contract_generator, "pick_deadline_hours", lambda: 3.0)
        db, origin, destination = _one_hop_pair()

        inputs = contract_generator.gather_contract_generation_inputs(db, stations=[origin, destination])
        batch = contract_generator.compute_contract_generation_batch(inputs)
        generated = contract_generator.write_contract_generation_batch(db, batch, now=_NOW)

        assert generated == 1
        assert len(db.added) == 1
        c = db.added[0]
        assert c.issuer_id == destination.id
        assert c.commodity_type == "ore"
        assert c.quantity == 100
        assert c.deadline == _NOW + timedelta(hours=3.0)
