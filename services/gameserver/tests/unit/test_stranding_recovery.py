"""WO-GWQ-STRANDING -- one-way-stranding recovery v1: Federation distress
beacon (any hull, -10 Terran Federation rep via faction_service.
apply_faction_rep_delta, 24h scaled cooldown on the new additive
Player.last_distress_at column, free transport to nearest fedspace sector)
+ Warp Jumper Slipdrive (quantum_jump_capable hulls only; 3-turn charge
debited at begin, scaled-deadline spin-up; completion teleports to the
nearest non-sink sector ignoring warp topology; fuel 50 + 10/hop,
monotonic in undirected hop distance).

DB-free: hand-built fakes, no real DB/app (mirrors test_warp_gate_toll.py /
test_formation_knowledge.py's _FakeQuery/_FakeSession + fake-query-filter-
interpreter conventions). `apply_faction_rep_delta`, `docking_service.
release`, and `movement_service.MovementService` are monkeypatched at their
SOURCE module (the two service modules under test import them LOCALLY,
inside the function body, so patching the source attribute is what the
local `from X import Y` re-resolves against at call time) -- the real
target under test is `distress_service.use_distress_beacon` /
`slipdrive_service.begin_charge` / `slipdrive_service.complete_charge` and
their private BFS/graph helpers.

Acceptance-criteria map (WO-GWQ-STRANDING, 11 numbered + 4 "Plus" items):
  1  TestDistressBeaconEndToEnd::test_stranded_player_escapes_state_unchanged_except_rep_and_sector
  2  TestDistressBeaconEndToEnd::test_rep_delta_via_spy_never_direct_faction_query
  3  TestCooldownBoundary::test_23h59_blocked / test_24h00_exact_boundary_allowed / test_24h01_allowed
  4  TestDistressBeaconEndToEnd::test_allowed_from_a_well_connected_non_stranded_sector
  5  TestStatusNeverRaises::test_beacon_status_empty_defaults / test_slipdrive_status_empty_defaults /
     test_slipdrive_status_mid_charge_shape
  6  TestSlipdriveHullGate::test_non_quantum_jump_capable_hull_rejected
  7  TestSlipdriveChargeLifecycle::test_early_completion_blocked / test_escapes_inbound_only_topology
  8  TestFuelMonotonic::test_fuel_cost_strictly_increasing_in_hops / test_e2e_fuel_deduction_matches_formula
  9  TestNearestTargetSelection::test_slipdrive_prefers_closer_hop_over_lower_id /
     test_slipdrive_tiebreak_lowest_sector_id_same_hop / test_distress_tiebreak_lowest_sector_id_same_hop
  10 TestUntouchedFiles::test_movement_and_regional_governance_have_no_stranding_recovery_coupling
  11 TestMigrationAdditiveOnly (module-level, file-text/AST based)

  Plus: TestNoFedspace::test_no_fedspace_anywhere_raises_400_shaped_error
        TestSlipdriveHullGate::test_early_completion_blocked (tiebreak covered under #9)
        TestHarmonizingRefusal::test_beacon_refuses_harmonizing_ship /
        test_slipdrive_begin_refuses_harmonizing_ship
        TestConcurrentRace::test_beacon_second_fire_within_cooldown_rejected /
        test_slipdrive_second_begin_while_charging_rejected
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.faction import FactionType
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType
from src.services import distress_service, slipdrive_service
from src.services.distress_service import DistressError
from src.services.slipdrive_service import SlipdriveError

FUEL_BASE = slipdrive_service.SLIPDRIVE_FUEL_BASE
FUEL_PER_HOP = slipdrive_service.SLIPDRIVE_FUEL_PER_HOP
CHARGE_TURN_COST = slipdrive_service.SLIPDRIVE_CHARGE_TURN_COST
CHARGE_HOURS = slipdrive_service.SLIPDRIVE_CHARGE_HOURS


# --- shared fakes (mirrors test_warp_gate_toll.py / test_formation_knowledge.py) --- #


class _FakeQuery:
    def __init__(self, first: Any = None, all: Optional[List[Any]] = None) -> None:
        self._first = first
        self._all = all if all is not None else []

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    """Dispatches `db.query(*entities)` on the IDENTITY of entities[0] --
    generalizes test_warp_gate_toll.py's class-keyed dispatch to also
    recognize Core-table Column objects (Sector.id / sector_warps.c.*),
    since sector_warps is a bare association Table, not a mapped entity
    (mirrors quantum_service._load_sector_points' own query shape)."""

    def __init__(
        self,
        *,
        player: Any = None,
        spec: Any = None,
        sectors: Optional[List[Any]] = None,
        warps: Optional[List[Any]] = None,
    ) -> None:
        self._player = player
        self._spec = spec
        self._sectors = sectors or []
        self._warps = warps or []
        self.flush_calls = 0
        self.deleted: List[Any] = []

    def query(self, *entities: Any) -> _FakeQuery:
        assert entities, "query() called with no entities"
        head = entities[0]
        if head is __import__("src.models.player", fromlist=["Player"]).Player:
            return _FakeQuery(first=self._player)
        if head is ShipSpecification:
            return _FakeQuery(first=self._spec)
        if head is Sector.id:
            return _FakeQuery(all=self._sectors)
        if head is sector_warps.c.source_sector_id:
            return _FakeQuery(all=self._warps)
        raise AssertionError(f"unexpected query for {entities!r}")

    def refresh(self, obj: Any) -> None:
        pass

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")

    def rollback(self) -> None:
        pass


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        current_sector_id=1,
        current_region_id=uuid.uuid4(),
        current_ship=None,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        current_planet_id=None,
        turns=1000,
        lifetime_turns_spent=0,
        last_distress_at=None,
        credits=10000,
        # slipdrive_service.begin_charge calls turn_service.regenerate_turns
        # before its affordability check (THE FROZEN HOOK -- every spend site
        # does). max_turns=1000 satisfies its stored-cap fallback if
        # RankingService.calculate_max_turns can't resolve a bare
        # SimpleNamespace's rank; last_turn_regeneration=None (with no
        # created_at either) hits regenerate_turns's "no anchor at all"
        # branch, which sets the anchor and returns immediately WITHOUT
        # mutating turns -- keeps turn-cost assertions deterministic.
        max_turns=1000,
        last_turn_regeneration=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_ship(**overrides: Any) -> SimpleNamespace:
    """SimpleNamespace is fine here: distress_service never calls
    flag_modified on the ship (only a plain `ship.sector_id =` set)."""
    base = dict(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        status=ShipStatus.IN_SPACE,
        is_destroyed=False,
        sector_id=1,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _real_ship(**overrides: Any) -> Ship:
    """A REAL ORM instance: slipdrive_service calls flag_modified(ship,
    "equipment_slots"), which requires `_sa_instance_state` -- a bare
    SimpleNamespace raises AttributeError (test_warp_gate_toll.py's
    `_fake_tunnel` precedent). `cargo` is accepted for constructor
    compatibility but unused by the service -- Slipdrive fuel is a
    player.credits cost (WO: "fuel 50+10/hop credits"), not a cargo draw."""
    base = dict(
        id=uuid.uuid4(),
        type=ShipType.WARP_JUMPER,
        status=ShipStatus.IN_SPACE,
        is_destroyed=False,
        sector_id=1,
        equipment_slots={},
    )
    base.update(overrides)
    return Ship(**base)


def _sector(sector_id: int, *, fedspace: bool = False, name: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        sector_id=sector_id,
        region_id=uuid.uuid4(),
        name=name or f"Sector {sector_id}",
        special_features=["fedspace"] if fedspace else [],
    )


def _edge(a: SimpleNamespace, b: SimpleNamespace, *, bidirectional: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        source_sector_id=a.id, destination_sector_id=b.id, is_bidirectional=bidirectional,
    )


def _install_movement_and_docking_stubs(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Both service modules do a LOCAL `from src.services.X import Y` inside
    the function body -- patching the SOURCE module attribute is what that
    re-resolves against at call time. Records calls so tests can assert the
    arrival path actually ran."""
    calls: Dict[str, Any] = {"release": [], "presence": []}

    def _fake_release(db: Any, station: Any, player: Any) -> bool:
        calls["release"].append((station, player))
        return False

    class _FakeMovementService:
        def __init__(self, db: Any) -> None:
            self.db = db

        def _update_player_presence(self, player: Any, old_sector_id: int, new_sector_id: int) -> None:
            calls["presence"].append((old_sector_id, new_sector_id))

    monkeypatch.setattr("src.services.docking_service.release", _fake_release)
    monkeypatch.setattr("src.services.movement_service.MovementService", _FakeMovementService)
    return calls


def _spy_rep_delta(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    calls: List[Any] = []

    def _spy(db: Any, player_id: Any, faction_type: Any, delta: int, reason: str) -> None:
        calls.append((player_id, faction_type, delta, reason))
        return None

    monkeypatch.setattr(distress_service, "apply_faction_rep_delta", _spy)
    return calls


# --- Accept #1/#2/#4: distress beacon end-to-end ---------------------------- #


@pytest.mark.unit
class TestDistressBeaconEndToEnd:
    def test_stranded_player_escapes_state_unchanged_except_rep_and_sector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        origin = _sector(1)
        mid = _sector(2)
        fed = _sector(3, fedspace=True)
        sectors = [origin, mid, fed]
        warps = [_edge(origin, mid), _edge(mid, fed)]

        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(
            current_sector_id=origin.sector_id, current_ship=ship, turns=500, credits=12345,
        )
        db = _FakeSession(player=player, sectors=sectors, warps=warps)
        rep_calls = _spy_rep_delta(monkeypatch)
        move_calls = _install_movement_and_docking_stubs(monkeypatch)

        pinned_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = distress_service.use_distress_beacon(db, player.id, now=pinned_now)

        assert result["destination_sector_id"] == fed.sector_id
        assert result["hops"] == 2
        assert result["reputation_delta"] == -10
        assert player.current_sector_id == fed.sector_id
        assert ship.sector_id == fed.sector_id
        assert player.turns == 500  # unchanged -- "free transport"
        assert player.credits == 12345  # unchanged
        assert player.last_distress_at == pinned_now
        assert rep_calls == [(player.id, FactionType.FEDERATION, -10, distress_service.DISTRESS_REASON)]
        assert move_calls["presence"] == [(origin.sector_id, fed.sector_id)]
        assert db.flush_calls == 1  # flush-only -- route commits

    def test_rep_delta_via_spy_never_direct_faction_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """distress_service itself must never touch Faction/Reputation --
        the fake session has NO spec for either, so any direct query would
        raise AssertionError from _FakeSession.query's else-branch."""
        origin = _sector(1)
        fed = _sector(2, fedspace=True)
        db = _FakeSession(
            player=_fake_player(current_sector_id=1, current_ship=_fake_ship()),
            sectors=[origin, fed],
            warps=[_edge(origin, fed)],
        )
        rep_calls = _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)

        distress_service.use_distress_beacon(db, db._player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(rep_calls) == 1  # the ONLY rep mutation path

    def test_allowed_from_a_well_connected_non_stranded_sector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not gated on actually being stranded -- a player in a normal,
        well-connected sector can still fire the panic button."""
        a = _sector(1)
        b = _sector(2)
        fed = _sector(3, fedspace=True)
        c = _sector(4)
        sectors = [a, b, fed, c]
        # `a` has TWO outbound-equivalent edges (fully connected, not a sink)
        warps = [_edge(a, b), _edge(a, c), _edge(b, fed)]
        ship = _fake_ship(sector_id=a.sector_id)
        player = _fake_player(current_sector_id=a.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=sectors, warps=warps)
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)

        result = distress_service.use_distress_beacon(db, player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert result["destination_sector_id"] == fed.sector_id


# --- Accept #3: cooldown boundary -------------------------------------------- #


@pytest.mark.unit
class TestCooldownBoundary:
    def _setup(self, hours_ago: float):
        pinned_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        origin = _sector(1)
        fed = _sector(2, fedspace=True)
        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(
            current_sector_id=origin.sector_id,
            current_ship=ship,
            last_distress_at=pinned_now - timedelta(hours=hours_ago),
        )
        db = _FakeSession(player=player, sectors=[origin, fed], warps=[_edge(origin, fed)])
        return db, player, pinned_now

    def test_23h59_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db, player, now = self._setup(hours_ago=23 + 59 / 60)
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)
        with pytest.raises(DistressError) as exc:
            distress_service.use_distress_beacon(db, player.id, now=now)
        assert exc.value.status_code == 429
        assert "remaining_seconds" in exc.value.payload

    def test_24h00_exact_boundary_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db, player, now = self._setup(hours_ago=distress_service.DISTRESS_COOLDOWN_HOURS)
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)
        result = distress_service.use_distress_beacon(db, player.id, now=now)
        assert result["destination_sector_id"] == 2

    def test_24h01_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db, player, now = self._setup(hours_ago=24 + 1 / 60)
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)
        result = distress_service.use_distress_beacon(db, player.id, now=now)
        assert result["destination_sector_id"] == 2


# --- Accept #5: status never raises ------------------------------------------ #


@pytest.mark.unit
class TestStatusNeverRaises:
    def test_beacon_status_empty_defaults(self) -> None:
        player = _fake_player(last_distress_at=None)
        status = distress_service.get_status(None, player)  # type: ignore[arg-type]
        assert status == {"available": True, "cooldown_until": None, "last_used_at": None}

    def test_slipdrive_status_empty_defaults(self) -> None:
        ship = _real_ship(equipment_slots={})
        player = _fake_player(current_ship=ship)
        status = slipdrive_service.get_status(None, player)  # type: ignore[arg-type]
        assert status == {"charging": False, "charge_deadline": None, "ready": False}

    def test_slipdrive_status_mid_charge_shape(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        deadline = now + timedelta(minutes=15)
        ship = _real_ship(equipment_slots={
            slipdrive_service._EQUIPMENT_KEY: {
                "origin_sector_id": 7, "charge_started_at": now.isoformat(), "deadline": deadline.isoformat(),
            }
        })
        player = _fake_player(current_sector_id=7, current_ship=ship)
        status = slipdrive_service.get_status(None, player, now=now)  # type: ignore[arg-type]
        assert status["charging"] is True
        assert status["ready"] is False
        assert status["charge_deadline"] == deadline.isoformat()

        # ready once past deadline
        status_ready = slipdrive_service.get_status(None, player, now=deadline + timedelta(seconds=1))  # type: ignore[arg-type]
        assert status_ready["ready"] is True

        # cancelled-by-movement: player's sector no longer matches the charge's origin
        player.current_sector_id = 8
        status_moved = slipdrive_service.get_status(None, player, now=now)  # type: ignore[arg-type]
        assert status_moved["charging"] is False
        assert status_moved["cancelled_by_movement"] is True


# --- Accept #6: Slipdrive hull gate ------------------------------------------ #


@pytest.mark.unit
class TestSlipdriveHullGate:
    def test_non_quantum_jump_capable_hull_rejected(self) -> None:
        ship = _real_ship(type=ShipType.SCOUT_SHIP)
        player = _fake_player(current_ship=ship, turns=1000)
        spec = SimpleNamespace(type=ShipType.SCOUT_SHIP, quantum_jump_capable=False)
        db = _FakeSession(player=player, spec=spec)
        with pytest.raises(SlipdriveError, match="quantum-jump-capable"):
            slipdrive_service.begin_charge(db, player.id)


# --- Accept #7: charge lifecycle --------------------------------------------- #


@pytest.mark.unit
class TestSlipdriveChargeLifecycle:
    def _spec(self) -> SimpleNamespace:
        return SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)

    def test_early_completion_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        target = _sector(2)  # non-sink candidate, irrelevant to this test
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        db = _FakeSession(
            player=player, spec=self._spec(), sectors=[origin, target], warps=[_edge(origin, target)],
        )
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        begin = slipdrive_service.begin_charge(db, player.id, now=t0)
        assert begin["turns_spent"] == CHARGE_TURN_COST
        assert player.turns == 100 - CHARGE_TURN_COST

        with pytest.raises(SlipdriveError, match="still charging"):
            slipdrive_service.complete_charge(db, player.id, now=t0)
        # no refund from the blocked-early attempt
        assert player.turns == 100 - CHARGE_TURN_COST

    def test_escapes_inbound_only_topology(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """origin is a REAL WARP_SINK by the topological definition: it has
        an inbound ONE-WAY edge from `feeder` and zero outbound edges of its
        own -- a graph-legal move could never leave it. Slipdrive's
        undirected-adjacency BFS still finds `feeder` at hop 1 and the
        teleport completes regardless of the one-way direction."""
        origin = _sector(1)  # the sink -- no outbound edges anywhere below
        feeder = _sector(2)  # has outbound elsewhere -> NOT a sink
        elsewhere = _sector(3)
        sectors = [origin, feeder, elsewhere]
        warps = [
            _edge(feeder, origin, bidirectional=False),  # feeder -> origin ONLY
            _edge(feeder, elsewhere, bidirectional=True),  # gives feeder outbound reach
        ]
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        db = _FakeSession(player=player, spec=self._spec(), sectors=sectors, warps=warps)
        _install_movement_and_docking_stubs(monkeypatch)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        slipdrive_service.begin_charge(db, player.id, now=t0)
        ready_at = t0 + timedelta(hours=CHARGE_HOURS, seconds=1)
        result = slipdrive_service.complete_charge(db, player.id, now=ready_at)

        assert result["destination_sector_id"] == feeder.sector_id
        assert result["hops"] == 1
        assert player.current_sector_id == feeder.sector_id
        assert ship.equipment_slots.get(slipdrive_service._EQUIPMENT_KEY) is None  # charge cleared

    def test_movement_mid_charge_cancels_no_refund(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        target = _sector(2)
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        db = _FakeSession(
            player=player, spec=self._spec(), sectors=[origin, target], warps=[_edge(origin, target)],
        )
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        slipdrive_service.begin_charge(db, player.id, now=t0)
        turns_after_begin = player.turns

        # ordinary movement happens (lazily observed, no movement_service hook)
        player.current_sector_id = target.sector_id

        ready_at = t0 + timedelta(hours=CHARGE_HOURS, seconds=1)
        with pytest.raises(SlipdriveError, match="cancelled"):
            slipdrive_service.complete_charge(db, player.id, now=ready_at)
        assert player.turns == turns_after_begin  # no refund
        assert ship.equipment_slots.get(slipdrive_service._EQUIPMENT_KEY) is None  # cleared, not stuck forever


# --- Accept #8: fuel monotonic ------------------------------------------------ #


@pytest.mark.unit
class TestFuelMonotonic:
    def test_fuel_cost_strictly_increasing_in_hops(self) -> None:
        f1 = slipdrive_service._fuel_cost(1)
        f3 = slipdrive_service._fuel_cost(3)
        f6 = slipdrive_service._fuel_cost(6)
        assert f1 == FUEL_BASE + FUEL_PER_HOP * 1
        assert f3 == FUEL_BASE + FUEL_PER_HOP * 3
        assert f6 == FUEL_BASE + FUEL_PER_HOP * 6
        assert f1 < f3 < f6

    def test_e2e_fuel_deduction_matches_formula(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # chain of 4 hops: origin -> s2 -> s3 -> s4 -> target(non-sink, but
        # since ALL of these are mutually reachable and none is a sink by
        # construction, the nearest match is origin itself at hop 0 unless
        # origin is made a sink; make origin sink like the topology test.
        origin = _sector(1)
        s2, s3, s4 = _sector(2), _sector(3), _sector(4)
        sectors = [origin, s2, s3, s4]
        warps = [
            _edge(s2, origin, bidirectional=False),  # origin: inbound only -> sink
            _edge(s2, s3, bidirectional=True),
            _edge(s3, s4, bidirectional=True),
        ]
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        spec = SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)
        db = _FakeSession(player=player, spec=spec, sectors=sectors, warps=warps)
        _install_movement_and_docking_stubs(monkeypatch)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        slipdrive_service.begin_charge(db, player.id, now=t0)
        ready_at = t0 + timedelta(hours=CHARGE_HOURS, seconds=1)

        result = slipdrive_service.complete_charge(db, player.id, now=ready_at)
        assert result["hops"] == 1  # s2 is reached at hop 1 and is non-sink
        expected_fuel = slipdrive_service._fuel_cost(1)
        assert result["fuel_spent"] == expected_fuel
        # Fuel is credit-denominated (WO: "fuel 50+10/hop credits"), NOT a
        # ship.cargo["fuel"] commodity -- complete_charge debits player.credits.
        assert player.credits == 10000 - expected_fuel
        assert result["credits_remaining"] == player.credits


# --- Accept #9: nearest-target selection + tiebreak -------------------------- #


@pytest.mark.unit
class TestNearestTargetSelection:
    def test_slipdrive_prefers_closer_hop_over_lower_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        near = _sector(50)  # hop 1, non-sink, HIGHER sector_id
        far = _sector(2)    # hop 2, non-sink, LOWER sector_id
        bridge = _sector(3)
        sectors = [origin, near, far, bridge]
        warps = [
            _edge(bridge, origin, bidirectional=False),  # origin is a sink
            _edge(bridge, near, bidirectional=True),      # near @ hop 1 via bridge
            _edge(near, far, bidirectional=True),         # far @ hop 2
        ]
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        spec = SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)
        db = _FakeSession(player=player, spec=spec, sectors=sectors, warps=warps)
        _install_movement_and_docking_stubs(monkeypatch)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        slipdrive_service.begin_charge(db, player.id, now=t0)
        ready_at = t0 + timedelta(hours=CHARGE_HOURS, seconds=1)

        result = slipdrive_service.complete_charge(db, player.id, now=ready_at)
        # bridge (hop 1, non-sink too since it has outbound edges) wins over
        # both near/far by hop distance -- bridge is closer than near
        assert result["hops"] == 1
        assert result["destination_sector_id"] == bridge.sector_id

    def test_slipdrive_tiebreak_lowest_sector_id_same_hop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)  # sink -- inbound-only edges below, no outbound
        candidate_high = _sector(99)
        candidate_low = _sector(20)
        sectors = [origin, candidate_high, candidate_low]
        # both candidates reachable at hop 1 (undirected adjacency) and both
        # non-sink (each has its own outbound edge TO origin); origin itself
        # has zero outbound edges, so it correctly fails the "non-sink" match
        # at hop 0 and the BFS must actually compare the two hop-1 candidates
        warps = [
            _edge(candidate_high, origin, bidirectional=False),
            _edge(candidate_low, origin, bidirectional=False),
        ]
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        spec = SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)
        db = _FakeSession(player=player, spec=spec, sectors=sectors, warps=warps)
        _install_movement_and_docking_stubs(monkeypatch)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        slipdrive_service.begin_charge(db, player.id, now=t0)
        ready_at = t0 + timedelta(hours=CHARGE_HOURS, seconds=1)

        result = slipdrive_service.complete_charge(db, player.id, now=ready_at)
        assert result["destination_sector_id"] == candidate_low.sector_id

    def test_distress_tiebreak_lowest_sector_id_same_hop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        fed_high = _sector(99, fedspace=True)
        fed_low = _sector(20, fedspace=True)
        sectors = [origin, fed_high, fed_low]
        warps = [_edge(origin, fed_high), _edge(origin, fed_low)]
        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=sectors, warps=warps)
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)

        result = distress_service.use_distress_beacon(db, player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert result["destination_sector_id"] == fed_low.sector_id


# --- Plus: no fedspace anywhere ---------------------------------------------- #


@pytest.mark.unit
class TestNoFedspace:
    def test_no_fedspace_anywhere_raises_400_shaped_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        other = _sector(2)  # no fedspace flag anywhere in the galaxy
        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=[origin, other], warps=[_edge(origin, other)])
        _spy_rep_delta(monkeypatch)

        with pytest.raises(DistressError, match="no_fedspace") as exc:
            distress_service.use_distress_beacon(db, player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert exc.value.status_code == 400


# --- Plus: HARMONIZING refusal ------------------------------------------------ #


@pytest.mark.unit
class TestHarmonizingRefusal:
    def test_beacon_refuses_harmonizing_ship(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        fed = _sector(2, fedspace=True)
        ship = _fake_ship(sector_id=origin.sector_id, status=ShipStatus.HARMONIZING)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=[origin, fed], warps=[_edge(origin, fed)])
        rep_calls = _spy_rep_delta(monkeypatch)

        with pytest.raises(DistressError, match="harmonizing"):
            distress_service.use_distress_beacon(db, player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert rep_calls == []
        assert player.current_sector_id == origin.sector_id

    def test_slipdrive_begin_refuses_harmonizing_ship(self) -> None:
        ship = _real_ship(status=ShipStatus.HARMONIZING)
        player = _fake_player(current_ship=ship, turns=100)
        spec = SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)
        db = _FakeSession(player=player, spec=spec)
        with pytest.raises(SlipdriveError, match="harmonizing"):
            slipdrive_service.begin_charge(db, player.id)


# --- Plus: simulated concurrent race (sequential double-fire) ---------------- #


@pytest.mark.unit
class TestConcurrentRace:
    def test_beacon_second_fire_within_cooldown_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        origin = _sector(1)
        fed = _sector(2, fedspace=True)
        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=[origin, fed], warps=[_edge(origin, fed)])
        _spy_rep_delta(monkeypatch)
        _install_movement_and_docking_stubs(monkeypatch)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        distress_service.use_distress_beacon(db, player.id, now=now)
        with pytest.raises(DistressError) as exc:
            distress_service.use_distress_beacon(db, player.id, now=now + timedelta(seconds=1))
        assert exc.value.status_code == 429

    def test_slipdrive_second_begin_while_charging_rejected(self) -> None:
        origin = _sector(1)
        target = _sector(2)
        ship = _real_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=100)
        spec = SimpleNamespace(type=ShipType.WARP_JUMPER, quantum_jump_capable=True)
        db = _FakeSession(
            player=player, spec=spec, sectors=[origin, target], warps=[_edge(origin, target)],
        )
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        slipdrive_service.begin_charge(db, player.id, now=t0)
        with pytest.raises(SlipdriveError, match="already charging"):
            slipdrive_service.begin_charge(db, player.id, now=t0 + timedelta(seconds=1))


# --- Accept #10: movement_service.py / regional_governance_service.py untouched --- #


@pytest.mark.unit
class TestUntouchedFiles:
    def test_movement_and_regional_governance_have_no_stranding_recovery_coupling(self) -> None:
        gameserver_root = pathlib.Path(__file__).resolve().parents[2]
        forbidden = ("slipdrive", "distress_service", "last_distress_at", "recovery_service")
        for rel in ("src/services/movement_service.py", "src/services/regional_governance_service.py"):
            source = (gameserver_root / rel).read_text().lower()
            for needle in forbidden:
                assert needle not in source, f"{rel} unexpectedly references {needle!r}"


# --- Accept #11: migration is additive-only ---------------------------------- #

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "8b9aa2bd781d_add_player_last_distress_at.py"
)


@pytest.mark.unit
class TestMigrationAdditiveOnly:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_upgrade_only_adds_the_one_column(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert upgrade_src.count("op.add_column(") == 1
        assert "last_distress_at" in upgrade_src
        for banned in ("op.alter_column", "op.drop_column", "op.drop_table", "op.create_table", "op.drop_index", "op.create_index"):
            assert banned not in upgrade_src

    def test_down_revision_is_the_current_head(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        assigns = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns.get("down_revision") == "fea17cc334a8"
        assert assigns.get("revision") == "8b9aa2bd781d"
