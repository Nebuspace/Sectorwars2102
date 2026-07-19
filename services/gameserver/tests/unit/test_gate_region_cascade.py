"""WO-GWQ-GATE-CASCADE — region-termination cascade kernel for player-built
warp gates (ADR-0052 SK38 / ADR-0050 / warp-gates.md "Region-termination
cascade").

Exercised directly against warp_gate_service.cascade_region_gate_teardown, a
KERNEL WITH NO CALLER anywhere in src/ yet (the region-lifecycle epic wires
invocation later), with hand-built fakes (no DB, no app) -- mirrors
test_warp_gate_toll.py's / test_gate_construction_staging.py's
_FakeQuery/_FakeSession pattern exactly.

Acceptance-criteria map (WO-GWQ-GATE-CASCADE):
  1  TestBothEndpointsRemoved::test_active_gate_tunnel_deleted_gate_collapsed
  2  TestBothEndpointsRemoved::test_harmonizing_gate_also_processed
  3  TestRefundMath::test_exact_half_refund / test_odd_cost_floors_down
  4  TestIdempotency::test_reinvocation_pays_once
  5  TestIdempotency::test_gate_spanning_two_terminating_regions_pays_once
  6  TestNotification::test_self_addressed_system_message_created
  7  TestUnrelatedGatesUntouched::test_gate_outside_region_never_touched
  8  TestOrphanedOwner::test_orphaned_owner_no_refund_no_raise
  9  TestZeroCommits::test_kernel_never_commits
  10 TestPartialFailure::test_exception_propagates_uncaught_never_swallowed
  11 TestNoMatchingSectors::test_unknown_region_is_a_safe_no_op
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.message import Message
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector
from src.models.warp_gate import WarpGate, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import warp_gate_service

GATE_CASCADE_REFUND_DIVISOR = warp_gate_service.GATE_CASCADE_REFUND_DIVISOR


# --- shared fakes (mirrors test_warp_gate_toll.py / test_gate_construction_staging.py) --


class _FakeQuery:
    """Stands in for a SQLAlchemy Query. filter()/join()/order_by()/
    populate_existing()/with_for_update() are no-ops returning self — the
    test already controls exactly what's in the fake session, so predicates
    never need real evaluation. `seq` supports a query shape hit MORE THAN
    ONCE per call with DIFFERENT wanted results, consumed in call order."""

    def __init__(
        self,
        *,
        first: Any = None,
        count: int = 0,
        all: Optional[List[Any]] = None,
        seq: Optional[List[Any]] = None,
    ) -> None:
        self._first = first
        self._count = count
        self._all = all if all is not None else []
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def join(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def order_by(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first

    def count(self) -> int:
        return self._count

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    """Maps a model class to the fake query it should get. A query for a
    model with NO entry in `specs` raises — deliberate, proves ZERO queries
    for a given model on a code path (e.g. the orphaned-owner path issuing
    no Message-adjacent query beyond Player)."""

    def __init__(self, specs: Optional[Dict[type, _FakeQuery]] = None) -> None:
        self._specs = specs or {}
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.flush_calls = 0

    def query(self, model: type) -> _FakeQuery:
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("warp_gate_service functions are flush-only — the route/orchestrator commits")

    def rollback(self) -> None:
        pass


class _FailingFlushSession(_FakeSession):
    """Raises on the Nth flush() call (1-indexed) to simulate a mid-loop
    failure — proves the kernel neither swallows the exception nor reaches
    for db.commit() as a recovery path. A real Postgres session's
    .rollback() (the caller's job, never this kernel's) is what would
    reverse any already-flushed-but-uncommitted mutations from gates
    processed before the failure; this fake cannot replicate attribute
    un-mutation (SQLAlchemy expiration semantics), so this test proves what
    a DB-free fake CAN honestly prove: propagation, not attribute rollback."""

    def __init__(self, specs: Optional[Dict[type, _FakeQuery]] = None, *, fail_on_flush: int = 1) -> None:
        super().__init__(specs)
        self._fail_on_flush = fail_on_flush

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_calls == self._fail_on_flush:
            raise RuntimeError("simulated mid-cascade failure")


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=100_000)
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_tunnel(**overrides: Any) -> WarpTunnel:
    # A REAL ORM instance: WarpTunnel is read for .name only here, but
    # keeping it real (not SimpleNamespace) mirrors the sibling toll suite
    # and avoids any accidental reliance on ORM-only behavior.
    defaults = dict(
        id=uuid.uuid4(),
        name="Sector One Gate to Sector Two",
        origin_sector_id=uuid.uuid4(),
        destination_sector_id=uuid.uuid4(),
        type=WarpTunnelType.ARTIFICIAL,
        status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=False,
    )
    defaults.update(overrides)
    return WarpTunnel(**defaults)


def _fake_gate(player_id: Any, **overrides: Any) -> WarpGate:
    defaults = dict(
        id=uuid.uuid4(),
        beacon_id=uuid.uuid4(),
        player_id=player_id,
        status=WarpGateStatus.ACTIVE,
        warp_tunnel_id=uuid.uuid4(),
        hp=10_000,
        construction_cost=1_020_000,
    )
    defaults.update(overrides)
    return WarpGate(**defaults)


def _fake_region(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), name="Fringe Reaches")
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_sector(sector_id: int, region_id: Any) -> SimpleNamespace:
    return SimpleNamespace(sector_id=sector_id, region_id=region_id)


def _basic_session(region, sectors, gate, owner, tunnel, **extra_specs: Any) -> _FakeSession:
    specs: Dict[type, _FakeQuery] = {
        Region: _FakeQuery(first=region),
        Sector: _FakeQuery(all=sectors),
        WarpGate: _FakeQuery(all=[gate] if gate is not None else []),
        WarpTunnel: _FakeQuery(first=tunnel),
        Player: _FakeQuery(first=owner),
    }
    specs.update(extra_specs)
    return _FakeSession(specs)


# --- Accept #1/#2: both endpoints removed atomically, HARMONIZING included --


@pytest.mark.unit
class TestBothEndpointsRemoved:
    def test_active_gate_tunnel_deleted_gate_collapsed(self) -> None:
        region = _fake_region()
        owner = _fake_player(credits=1_000)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, status=WarpGateStatus.ACTIVE, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert gate.status == WarpGateStatus.COLLAPSED
        assert gate.warp_tunnel_id is None
        assert tunnel in db.deleted
        assert result["gates_processed"] == 1
        assert result["gate_ids"] == [str(gate.id)]
        # WarpGateBeacon is deliberately NEVER touched (ondelete=CASCADE trap
        # — see the kernel's docstring); no beacon query spec means any
        # attempted beacon query would already raise via _FakeSession.query.

    def test_harmonizing_gate_also_processed(self) -> None:
        """A HARMONIZING gate already carries its full construction_cost
        snapshot and a real FORMING tunnel — same sunk-cost exposure as
        ACTIVE, so canon's unqualified "a player-built warp gate" includes
        it."""
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel(status=WarpTunnelStatus.FORMING)
        gate = _fake_gate(
            owner.id, status=WarpGateStatus.HARMONIZING, warp_tunnel_id=tunnel.id,
            construction_cost=1_020_000,
        )
        sectors = [_fake_sector(5, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert gate.status == WarpGateStatus.COLLAPSED
        assert tunnel in db.deleted
        assert result["gates_processed"] == 1
        assert owner.credits == 510_000

    def test_cancelled_gate_skipped(self) -> None:
        """A gate the sector-touch query somehow still returned (stale
        caller reference) but that is already CANCELLED/COLLAPSED is a
        no-op — proves the status guard, independent of query filtering."""
        region = _fake_region()
        owner = _fake_player(credits=1_000)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, status=WarpGateStatus.CANCELLED, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert result["gates_processed"] == 0
        assert result["total_refunded"] == 0
        assert owner.credits == 1_000
        assert tunnel not in db.deleted


# --- Accept #3: refund math -------------------------------------------------


@pytest.mark.unit
class TestRefundMath:
    def test_exact_half_refund(self) -> None:
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert owner.credits == 510_000
        assert result["total_refunded"] == 510_000

    def test_odd_cost_floors_down(self) -> None:
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_000_001, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        warp_gate_service.cascade_region_gate_teardown(db, region.id)

        # floor(1_000_001 / 2) == 500_000, NOT 500_000.5 rounded anywhere.
        assert owner.credits == 500_000


# --- Accept #4/#5: idempotency ----------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    def test_reinvocation_pays_once(self) -> None:
        """The SAME gate object handed to a second call (simulating a real
        DB re-query after the first call's flush persisted COLLAPSED) must
        not pay a second refund."""
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]

        db1 = _basic_session(region, sectors, gate, owner, tunnel)
        first_result = warp_gate_service.cascade_region_gate_teardown(db1, region.id)
        assert first_result["gates_processed"] == 1
        assert owner.credits == 510_000

        # Second call re-queries and finds the SAME (now-COLLAPSED) gate.
        db2 = _basic_session(region, sectors, gate, owner, tunnel)
        second_result = warp_gate_service.cascade_region_gate_teardown(db2, region.id)

        assert second_result["gates_processed"] == 0
        assert second_result["total_refunded"] == 0
        assert owner.credits == 510_000  # unchanged — paid exactly once

    def test_gate_spanning_two_terminating_regions_pays_once(self) -> None:
        """One gate, source endpoint in region A, destination endpoint in
        region B. Both regions cascade in overlapping windows — whichever
        fires first pays; the second finds the gate already COLLAPSED."""
        region_a = _fake_region(name="Region A")
        region_b = _fake_region(name="Region B")
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=800_000, warp_tunnel_id=tunnel.id)

        sectors_a = [_fake_sector(10, region_a.id)]
        db_a = _basic_session(region_a, sectors_a, gate, owner, tunnel)
        result_a = warp_gate_service.cascade_region_gate_teardown(db_a, region_a.id)
        assert result_a["gates_processed"] == 1
        assert owner.credits == 400_000

        # Region B's cleanup orchestrator fires second, over the SAME gate
        # object (in reality: a fresh DB query that now sees COLLAPSED).
        sectors_b = [_fake_sector(20, region_b.id)]
        db_b = _basic_session(region_b, sectors_b, gate, owner, tunnel)
        result_b = warp_gate_service.cascade_region_gate_teardown(db_b, region_b.id)

        assert result_b["gates_processed"] == 0
        assert owner.credits == 400_000  # paid once, not twice


# --- Accept #6: notification ------------------------------------------------


@pytest.mark.unit
class TestNotification:
    def test_self_addressed_system_message_created(self) -> None:
        region = _fake_region(name="Fringe Reaches")
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel(name="Alpha Gate to Beta")
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        warp_gate_service.cascade_region_gate_teardown(db, region.id)

        messages = [m for m in db.added if isinstance(m, Message)]
        assert len(messages) == 1
        message = messages[0]
        assert message.sender_id == owner.id
        assert message.recipient_id == owner.id
        assert message.message_type == "system"
        assert "Alpha Gate to Beta" in message.content
        assert "Fringe Reaches" in message.content
        assert "510,000" in message.content

    def test_notification_failure_never_breaks_refund(self) -> None:
        """A failure while persisting the notice (db.add raises for the
        Message specifically — e.g. a JSONB/session hiccup) must still
        leave the refund applied and the teardown uninterrupted:
        _notify_gate_cascade_destroyed's own try/except is the guarantee,
        mirroring medal_service's identical best-effort convention."""

        class _AddRaisesForMessage(_FakeSession):
            def add(self, obj: Any) -> None:
                if isinstance(obj, Message):
                    raise RuntimeError("simulated notification persistence failure")
                super().add(obj)

        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _AddRaisesForMessage({
            Region: _FakeQuery(first=region),
            Sector: _FakeQuery(all=sectors),
            WarpGate: _FakeQuery(all=[gate]),
            WarpTunnel: _FakeQuery(first=tunnel),
            Player: _FakeQuery(first=owner),
        })

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        # Teardown completed normally despite the notification failure.
        assert result["gates_processed"] == 1
        assert gate.status == WarpGateStatus.COLLAPSED
        assert owner.credits == 510_000
        assert not any(isinstance(m, Message) for m in db.added)


# --- Accept #7: unrelated gates untouched -----------------------------------


@pytest.mark.unit
class TestUnrelatedGatesUntouched:
    def test_gate_outside_region_never_touched(self) -> None:
        """An unrelated gate is simply never returned by the sector-touch
        query (the fake's filter()/join() are no-ops, so the TEST controls
        membership) — proven by asserting its untouched state after the
        call, since it was never in the WarpGate query result set the
        kernel iterated over."""
        region = _fake_region()
        owner = _fake_player(credits=1_000)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]

        unrelated_owner = _fake_player(credits=42)
        unrelated_tunnel = _fake_tunnel()
        unrelated_gate = _fake_gate(
            unrelated_owner.id, status=WarpGateStatus.ACTIVE, warp_tunnel_id=unrelated_tunnel.id,
        )

        db = _basic_session(region, sectors, gate, owner, tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert str(unrelated_gate.id) not in result["gate_ids"]
        assert unrelated_gate.status == WarpGateStatus.ACTIVE
        assert unrelated_gate.warp_tunnel_id == unrelated_tunnel.id
        assert unrelated_tunnel not in db.deleted
        assert unrelated_owner.credits == 42


# --- Accept #8: orphaned owner -----------------------------------------------


@pytest.mark.unit
class TestOrphanedOwner:
    def test_orphaned_owner_no_refund_no_raise(self) -> None:
        region = _fake_region()
        tunnel = _fake_tunnel()
        ghost_player_id = uuid.uuid4()
        gate = _fake_gate(ghost_player_id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner=None, tunnel=tunnel)

        result = warp_gate_service.cascade_region_gate_teardown(db, region.id)

        assert gate.status == WarpGateStatus.COLLAPSED
        assert tunnel in db.deleted
        assert result["gates_processed"] == 1
        assert result["total_refunded"] == 0
        assert result["orphaned_owners"] == 1
        # No Message added — nobody to self-address it to.
        assert not any(isinstance(m, Message) for m in db.added)


# --- Accept #9: zero commits -------------------------------------------------


@pytest.mark.unit
class TestZeroCommits:
    def test_kernel_never_commits(self) -> None:
        """_FakeSession.commit() raises AssertionError if ever called — the
        happy path reaching a normal return without that error IS the proof
        this kernel is flush-only, mirroring test_warp_gate_toll.py's same
        implicit-raise convention."""
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]
        db = _basic_session(region, sectors, gate, owner, tunnel)

        warp_gate_service.cascade_region_gate_teardown(db, region.id)  # would raise if it committed
        assert db.flush_calls >= 1


# --- Accept #10: partial-failure propagation ---------------------------------


@pytest.mark.unit
class TestPartialFailure:
    def test_exception_propagates_uncaught_never_swallowed(self) -> None:
        """A failure injected on the FIRST flush() call (the mark-before-pay
        status flip) must propagate straight out of
        cascade_region_gate_teardown — never swallowed, never papered over
        with a commit-as-recovery. In a real Postgres session the caller's
        subsequent session.rollback() is what discards this gate's
        already-applied-in-Python mutations; that half is the caller's
        contract (house convention: this kernel commits nothing, ever), not
        something a DB-free fake can itself demonstrate."""
        region = _fake_region()
        owner = _fake_player(credits=0)
        tunnel = _fake_tunnel()
        gate = _fake_gate(owner.id, construction_cost=1_020_000, warp_tunnel_id=tunnel.id)
        sectors = [_fake_sector(1, region.id)]

        specs = {
            Region: _FakeQuery(first=region),
            Sector: _FakeQuery(all=sectors),
            WarpGate: _FakeQuery(all=[gate]),
            WarpTunnel: _FakeQuery(first=tunnel),
            Player: _FakeQuery(first=owner),
        }
        db = _FailingFlushSession(specs, fail_on_flush=1)

        with pytest.raises(RuntimeError, match="simulated mid-cascade failure"):
            warp_gate_service.cascade_region_gate_teardown(db, region.id)

        # The status flip happened in Python memory before the raising
        # flush (exactly the mark-before-pay order) — a real session's
        # rollback (never called here — that's the point) is what would
        # revert it; this fake cannot simulate that half.
        assert gate.status == WarpGateStatus.COLLAPSED
        # Refund was never reached — the raise came from the flush BEFORE
        # the owner-credit mutation.
        assert owner.credits == 0


# --- Accept #11: no matching sectors is a safe no-op -------------------------


@pytest.mark.unit
class TestNoMatchingSectors:
    def test_unknown_region_is_a_safe_no_op(self) -> None:
        region_id = uuid.uuid4()
        db = _FakeSession({
            Region: _FakeQuery(first=None),
            Sector: _FakeQuery(all=[]),
        })

        result = warp_gate_service.cascade_region_gate_teardown(db, region_id)

        assert result == {
            "region_id": str(region_id),
            "gates_processed": 0,
            "gate_ids": [],
            "total_refunded": 0,
            "orphaned_owners": 0,
        }
        # No WarpGate/WarpTunnel/Player query at all when there are no
        # sectors to match — proven by _FakeSession.query raising for any
        # model with no registered spec (WarpGate/WarpTunnel/Player are
        # absent above).
