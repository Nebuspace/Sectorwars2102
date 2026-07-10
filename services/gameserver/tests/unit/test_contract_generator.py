"""WO-ECON-CONTRACT-1-KERNEL lane 5 -- contract_generator.py.

DB-free: real SQLAlchemy filter-clause interpretation for Contract/Faction
queries (mirrors test_contract_service.py's interpreter); Sector /
sector_warps reads are dispatched by identity match on the query's head
entity, matching escape_pod_service.py / slipdrive_service.py's own test
convention. Stations are injected directly via `stations=` (the
generator's documented test-injection point) -- no Station query needed.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractIssuerType, ContractStatus
from src.models.faction import Faction
from src.models.sector import Sector, sector_warps
from src.models.station import StationClass
from src.services import contract_generator
from src.services.contract_generator import compute_cargo_delivery_payment


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
        return list(self._rows)


class _FakeSession:
    def __init__(
        self, *, sectors: Optional[List[Any]] = None, edges: Optional[List[Any]] = None,
        contracts: Optional[List[Any]] = None, factions: Optional[List[Any]] = None,
    ) -> None:
        self.sectors = sectors or []
        self.edges = edges or []
        self.contracts = contracts or []
        self.factions = factions or []
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


def _station(
    *, sector: SimpleNamespace, commodities: dict, faction_affiliation: Optional[str] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), sector_uuid=sector.id, commodities=commodities,
        faction_affiliation=faction_affiliation,
        station_class=StationClass.CLASS_3,  # neutral -- no 8/9/11 premium
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
        assert result == {"generated": 0, "stations_scanned": 1}

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
