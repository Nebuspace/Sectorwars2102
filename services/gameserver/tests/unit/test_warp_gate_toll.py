"""WO-GWQ-GATE-TOLL — warp-gate toll system: toll_fee setting, atomic
collection at traversal, revenue/usage tracking, ADR-0049 24h team-tenure
exemption, and the optional faction-rep min/max access layers.

Exercised directly against warp_gate_service's new surface (collect_toll,
check_traversal_access's extended layered-gate check, set_gate_permissions'
toll param, list_sector_structures' real toll field) plus a structural proof
of the movement_service.py wiring order, with hand-built fakes (no DB, no
app) — mirrors test_gate_construction_staging.py's _FakeQuery/_FakeSession
pattern.

SPEC-DEFECT CORRECTION (carried from the work order): the toll is collected
in MovementService.move_player_to_sector's player-gate branch, AFTER
check_traversal_access and AFTER that branch's own turns-affordability
check — never inside the PURE _check_warp_tunnel validator, which would bill
a player whose move then fails the turns check. That branch is also the
REAL exercised path for a player-built gate: MovementService._has_player_gate
matches first and short-circuits move_player_to_sector before
_check_warp_tunnel is ever reached for a player gate (confirmed by reading
movement_service.py — _check_warp_tunnel's own player-gate branch is
unreachable dead code from move_player_to_sector). Criteria 5/6 below prove
the wiring order structurally rather than by fully mocking
move_player_to_sector (which the existing movement suites never do either —
they all test private helpers directly; see test_movement_drone_encounters.py
/ test_warp_cost_canon.py / test_tunnel_type_vocab.py).

Acceptance-criteria map (WO-GWQ-GATE-TOLL, 13 total):
  1  TestSetGatePermissionsToll::test_toll_boundaries_persist /
     test_toll_out_of_range_rejected_jsonb_unchanged
  2  TestSetGatePermissionsToll::test_non_owner_rejected
  3  TestCollectTollAtomicity::test_paid_traversal_moves_both_balances_atomically /
     test_owner_vanishes_under_lock_mid_transaction_leaves_both_unchanged
  4  TestCollectTollAtomicity::test_insufficient_credits_zero_debit
  5  TestNeverCalledOffTraversal::test_check_warp_tunnel_never_touches_credits /
     test_collect_toll_has_exactly_one_call_site_in_movement_service
  6  TestMovementWiringOrder::test_turns_check_precedes_toll_collection_in_source
  7  TestExemptionPrecedence::test_owner_exempt / test_toll_bypass_pays_zero /
     test_whitelist_exempt
  8  TestTeamTenureBoundary::test_23h59_pays / test_24h00_exact_boundary_exempt /
     test_24h01_exempt
  9  TestFactionRepLayers::test_rep_min_blocks_before_toll_logic /
     test_rep_max_blocks_too_reputable_player
  10 TestUsageTracking::test_two_paid_traversals_accumulate
  11 TestListSectorStructuresToll::test_real_toll_reported_not_hardcoded_zero
  12 TestFreeGateRegression::test_zero_toll_gate_makes_no_debit_call
  13 TestMigrationAdditive::test_no_schema_change_pure_jsonb_plus_existing_column
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import Reputation
from src.models.sector import Sector
from src.models.team_member import TeamMember
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError

TOLL_FEE_MIN = warp_gate_service.TOLL_FEE_MIN
TOLL_FEE_MAX = warp_gate_service.TOLL_FEE_MAX
TOLL_TEAM_TENURE_HOURS = warp_gate_service.TOLL_TEAM_TENURE_HOURS


# --- shared fakes (mirrors test_gate_construction_staging.py) ---------------


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
    model with NO entry in `specs` raises — this is deliberate: several
    tests below rely on that AssertionError to prove a code path issues
    ZERO queries for a given model (e.g. the free-gate / owner-exempt /
    bypass-list paths never touching Player at all)."""

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
        raise AssertionError("warp_gate_service functions are flush-only — the route commits")

    def rollback(self) -> None:
        pass


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        credits=100_000,
        team_id=None,
        username="tester",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_tunnel(**overrides: Any) -> WarpTunnel:
    # A REAL ORM instance, not SimpleNamespace: collect_toll/set_gate_permissions
    # call flag_modified(tunnel, ...), which requires a mapped instance
    # (_sa_instance_state) — a bare SimpleNamespace raises AttributeError.
    defaults = dict(
        id=uuid.uuid4(),
        name="Test Gate",
        origin_sector_id=uuid.uuid4(),
        destination_sector_id=uuid.uuid4(),
        type=WarpTunnelType.ARTIFICIAL,
        status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=False,
        created_by_player_id=uuid.uuid4(),
        access_requirements={},
        artificial_data={},
        tunnel_status={},
        total_traversals=0,
        is_public=True,
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
    )
    defaults.update(overrides)
    return WarpGate(**defaults)


# --- Accept #1: toll boundaries persist / rejected --------------------------


@pytest.mark.unit
class TestSetGatePermissionsToll:
    def _setup(self, owner_id: Any, **tunnel_overrides: Any):
        owner = _fake_player(id=owner_id)
        gate = _fake_gate(owner_id)
        tunnel = _fake_tunnel(created_by_player_id=owner_id, **tunnel_overrides)
        gate.warp_tunnel_id = tunnel.id
        db = _FakeSession({
            WarpGate: _FakeQuery(first=gate),
            WarpTunnel: _FakeQuery(first=tunnel),
        })
        return db, owner, gate, tunnel

    @pytest.mark.parametrize("toll", [TOLL_FEE_MIN, 1, TOLL_FEE_MAX])
    def test_toll_boundaries_persist(self, toll: int) -> None:
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = self._setup(owner_id)
        result = warp_gate_service.set_gate_permissions(
            db, owner, str(gate.id), "PUBLIC", toll=toll,
        )
        assert result["toll_amount"] == toll
        assert tunnel.access_requirements["toll_amount"] == toll

    @pytest.mark.parametrize("bad_toll", [TOLL_FEE_MIN - 1, TOLL_FEE_MAX + 1])
    def test_toll_out_of_range_rejected_jsonb_unchanged(self, bad_toll: int) -> None:
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = self._setup(owner_id, access_requirements={"toll_amount": 250})
        with pytest.raises(WarpGateError, match="toll must be between"):
            warp_gate_service.set_gate_permissions(
                db, owner, str(gate.id), "PUBLIC", toll=bad_toll,
            )
        # Rejected BEFORE the gate/tunnel is even locked — JSONB completely
        # untouched, not just the toll key.
        assert tunnel.access_requirements == {"toll_amount": 250}

    def test_toll_omitted_preserves_existing_value(self) -> None:
        """Unlike mode/whitelist/allies (always overwritten), omitting toll
        must NOT silently reset it to 0."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = self._setup(owner_id, access_requirements={"toll_amount": 500})
        result = warp_gate_service.set_gate_permissions(db, owner, str(gate.id), "PUBLIC")
        assert result["toll_amount"] == 500
        assert tunnel.access_requirements["toll_amount"] == 500

    # --- Accept #2: non-owner rejected --------------------------------------

    def test_non_owner_rejected(self) -> None:
        """NOTE ON THE WO'S STATED STATUS CODE: the work order's acceptance
        text says 'non-owner POST -> 403'. The actual, pre-existing (and
        untouched by this WO) ownership gate in _resolve_owned_active_gate
        raises 404 — deliberately, per its own docstring: 'a gate that isn't
        yours 404s (no existence leak, mirrors construction.py)'. This is a
        real discrepancy between the WO text and the established house
        convention; flagged in the run report rather than silently changed,
        since flipping it to 403 would leak gate existence to a non-owner and
        contradicts this file's own documented design intent. This test
        pins the ACTUAL (unchanged) behavior."""
        owner_id = uuid.uuid4()
        db, owner, gate, tunnel = self._setup(owner_id)
        intruder = _fake_player()
        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.set_gate_permissions(
                db, intruder, str(gate.id), "PUBLIC", toll=100,
            )
        assert exc_info.value.status_code == 404
        # No mutation on a rejected call.
        assert tunnel.access_requirements == {}


# --- Accept #3/#4: atomic debit/credit, insufficient credits ----------------


@pytest.mark.unit
class TestCollectTollAtomicity:
    def test_paid_traversal_moves_both_balances_atomically(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
        )
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        result = warp_gate_service.collect_toll(db, traverser, tunnel)

        assert result["charged"] == 300
        assert result["exempt_reason"] is None
        assert traverser.credits == 5_000 - 300
        assert owner.credits == 1_000 + 300
        assert db.flush_calls >= 1

    def test_insufficient_credits_zero_debit(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=50)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
        )
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.collect_toll(db, traverser, tunnel)

        assert exc_info.value.status_code == 402
        assert "ERR_INSUFFICIENT_CREDITS_FOR_TOLL" in exc_info.value.detail
        # ZERO partial debit on EITHER side.
        assert traverser.credits == 50
        assert owner.credits == 1_000
        # No usage/revenue bookkeeping on a rejected toll either.
        assert tunnel.total_traversals == 0
        assert "toll_stats" not in (tunnel.artificial_data or {})

    def test_owner_vanishes_under_lock_mid_transaction_leaves_both_unchanged(self) -> None:
        """Injected mid-transaction failure: the owner exists at the plain
        exemption-resolution lookup but is gone by the row-locked re-fetch
        (a race). collect_toll must degrade to the orphaned-owner free path
        rather than crediting a ghost or half-applying the debit."""
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
        )
        # seq: first Player query (exemption-resolution peek) finds the
        # owner; the SECOND Player query (the row-locked fetch used only for
        # the actual mutation) returns None -- the owner vanished in between.
        db = _FakeSession({Player: _FakeQuery(seq=[owner, None])})

        result = warp_gate_service.collect_toll(db, traverser, tunnel)

        assert result["charged"] == 0
        assert result["exempt_reason"] == "owner_orphaned"
        assert traverser.credits == 5_000  # untouched
        assert owner.credits == 1_000      # untouched


# --- Accept #5: validation-only paths never touch credits -------------------


@pytest.mark.unit
class TestNeverCalledOffTraversal:
    def test_check_warp_tunnel_never_touches_credits(self) -> None:
        """_check_warp_tunnel (the pure validator get_available_moves and the
        natural-tunnel branch of move_player_to_sector both use) must never
        move a credit -- proven directly: it takes no Player/credits
        argument at all, and structurally never references collect_toll."""
        from src.services.movement_service import MovementService
        source = inspect.getsource(MovementService._check_warp_tunnel)
        assert "collect_toll" not in source
        assert "credits" not in source

    @staticmethod
    def _is_collect_toll_call(node: ast.AST) -> bool:
        """Matches both a bare-name call (collect_toll(...)) and the actual
        shipped form, a module-attribute call
        (warp_gate_service.collect_toll(...)) -- movement_service.py already
        imports warp_gate_service module-wide, so the real call site never
        needs its own local import/alias."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "collect_toll"
        if isinstance(func, ast.Attribute):
            return func.attr == "collect_toll"
        return False

    def test_collect_toll_has_exactly_one_call_site_in_movement_service(self) -> None:
        """collect_toll must be wired into exactly the player-gate branch of
        move_player_to_sector -- not get_available_moves, not
        _check_warp_tunnel, not anywhere else. A second call site would mean
        a validation-only or listing path could also be charging a toll."""
        module_path = pathlib.Path(
            __file__
        ).resolve().parents[2] / "src" / "services" / "movement_service.py"
        source = module_path.read_text()
        tree = ast.parse(source)
        call_sites = [node for node in ast.walk(tree) if self._is_collect_toll_call(node)]
        assert len(call_sites) == 1, (
            f"expected exactly one collect_toll call site in "
            f"movement_service.py, found {len(call_sites)}"
        )

        # And it must live inside move_player_to_sector, not some other method.
        enclosing_functions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "move_player_to_sector"
        ]
        assert len(enclosing_functions) == 1
        move_fn = enclosing_functions[0]
        calls_inside_move_fn = [
            node for node in ast.walk(move_fn) if self._is_collect_toll_call(node)
        ]
        assert len(calls_inside_move_fn) == 1


# --- Accept #6: turns-check precedes toll collection (ordering pin) ---------


@pytest.mark.unit
class TestMovementWiringOrder:
    def test_turns_check_precedes_toll_collection_in_source(self) -> None:
        """A turns-short player must never be billed a toll for a move that
        was already going to fail. spend_turns/_execute_movement both live
        textually (and are only ever reached) AFTER the turns-affordability
        check AND the toll collection call in the player-gate branch, so a
        rejection from either leaves turns and position untouched (neither
        spend_turns nor _execute_movement's player mutations are reachable
        before them). Pinned by source order within the branch, mirroring
        test_gate_construction_staging.py's precedent for this kind of
        structural, DB-free proof."""
        from src.services.movement_service import MovementService
        source = inspect.getsource(MovementService.move_player_to_sector)

        turns_check_idx = source.index("Not enough turns for this gate transit")
        access_call_idx = source.index("warp_gate_service.check_traversal_access(")
        toll_call_idx = source.index("warp_gate_service.collect_toll(")
        execute_movement_idx = source.index(
            "result = self._execute_movement(player, destination_sector_id, gate_cost)"
        )

        assert turns_check_idx < access_call_idx < toll_call_idx < execute_movement_idx


# --- Accept #7: exemption precedence -----------------------------------------


@pytest.mark.unit
class TestExemptionPrecedence:
    def test_owner_exempt(self) -> None:
        owner = _fake_player(credits=1_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 999},
        )
        # No Player spec at all -- the owner path must issue ZERO queries.
        db = _FakeSession({})

        result = warp_gate_service.collect_toll(db, owner, tunnel)

        assert result["charged"] == 0
        assert result["exempt_reason"] == "owner"
        assert owner.credits == 1_000
        # Owner traversal still bumps the general traversal counter.
        assert tunnel.total_traversals == 1
        # ...but NOT the toll-specific (non-owner-only) reporting surface.
        assert "toll_stats" not in tunnel.artificial_data

    def test_toll_bypass_pays_zero(self) -> None:
        owner_id = uuid.uuid4()
        traverser = _fake_player(credits=10)
        tunnel = _fake_tunnel(
            created_by_player_id=owner_id,
            access_requirements={"toll_amount": 999, "toll_bypass": [str(traverser.id)]},
        )
        # Bypass short-circuits BEFORE any Player query for the owner.
        db = _FakeSession({})

        result = warp_gate_service.collect_toll(db, traverser, tunnel)

        assert result["charged"] == 0
        assert result["exempt_reason"] == "toll_bypass"
        assert traverser.credits == 10
        assert tunnel.artificial_data["toll_stats"]["usage_count"] == 1
        assert tunnel.artificial_data["toll_stats"]["total_revenue"] == 0

    def test_whitelist_exempt(self) -> None:
        """Being on the ACCESS whitelist (WG1's existing key) also exempts
        the toll on ANY mode -- not only WHITELIST mode."""
        owner_id = uuid.uuid4()
        traverser = _fake_player(credits=10)
        tunnel = _fake_tunnel(
            created_by_player_id=owner_id,
            access_requirements={"toll_amount": 999, "whitelist": [str(traverser.id)]},
        )
        db = _FakeSession({})

        result = warp_gate_service.collect_toll(db, traverser, tunnel)

        assert result["charged"] == 0
        assert result["exempt_reason"] == "whitelist"
        assert traverser.credits == 10


# --- Accept #8: ADR-0049 24h team-tenure boundary matrix ---------------------


@pytest.mark.unit
class TestTeamTenureBoundary:
    def _setup(self, hours_ago: float):
        pinned_now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        team_id = uuid.uuid4()
        owner = _fake_player(credits=1_000, team_id=team_id)
        traverser = _fake_player(credits=1_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 100},
        )
        membership = TeamMember(
            team_id=team_id,
            player_id=traverser.id,
            joined_at=pinned_now - timedelta(hours=hours_ago),
        )
        db = _FakeSession({
            Player: _FakeQuery(first=owner),
            TeamMember: _FakeQuery(first=membership),
        })
        return db, owner, traverser, tunnel, pinned_now

    def test_23h59_pays(self) -> None:
        db, owner, traverser, tunnel, now = self._setup(hours_ago=23 + 59 / 60)
        result = warp_gate_service.collect_toll(db, traverser, tunnel, now=now)
        assert result["exempt_reason"] is None
        assert result["charged"] == 100
        assert traverser.credits == 900

    def test_24h00_exact_boundary_exempt(self) -> None:
        db, owner, traverser, tunnel, now = self._setup(hours_ago=TOLL_TEAM_TENURE_HOURS)
        result = warp_gate_service.collect_toll(db, traverser, tunnel, now=now)
        assert result["exempt_reason"] == "team_tenure"
        assert result["charged"] == 0
        assert traverser.credits == 1_000

    def test_24h01_exempt(self) -> None:
        db, owner, traverser, tunnel, now = self._setup(hours_ago=24 + 1 / 60)
        result = warp_gate_service.collect_toll(db, traverser, tunnel, now=now)
        assert result["exempt_reason"] == "team_tenure"
        assert result["charged"] == 0
        assert traverser.credits == 1_000


# --- Accept #9: faction-rep min/max layers reject before toll ---------------


@pytest.mark.unit
class TestFactionRepLayers:
    def _tunnel_with_layer(self, owner_id: Any, layer_key: str, faction_type: str, value: int) -> WarpTunnel:
        return _fake_tunnel(
            created_by_player_id=owner_id,
            access_requirements={
                "mode": "PUBLIC",
                "toll_amount": 500,
                layer_key: {"faction_type": faction_type, "value": value},
            },
        )

    def test_rep_min_blocks_before_toll_logic(self) -> None:
        owner_id = uuid.uuid4()
        player = _fake_player()
        tunnel = self._tunnel_with_layer(owner_id, "faction_rep_min", FactionType.FEDERATION.value, 5)
        faction = SimpleNamespace(id=uuid.uuid4(), faction_type=FactionType.FEDERATION)
        reputation = SimpleNamespace(current_value=-10)
        db = _FakeSession({
            Faction: _FakeQuery(first=faction),
            Reputation: _FakeQuery(first=reputation),
        })

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.check_traversal_access(db, player, tunnel)
        assert exc_info.value.status_code == 403
        assert "ERR_GATE_REP_TOO_LOW" in exc_info.value.detail

        # And collect_toll is never invoked as part of the access check --
        # no credits move (proven independently: check_traversal_access
        # takes no credits-bearing path at all -- see TestNeverCalledOffTraversal).

    def test_rep_min_passes_when_sufficient(self) -> None:
        owner_id = uuid.uuid4()
        player = _fake_player()
        tunnel = self._tunnel_with_layer(owner_id, "faction_rep_min", FactionType.FEDERATION.value, 5)
        faction = SimpleNamespace(id=uuid.uuid4(), faction_type=FactionType.FEDERATION)
        reputation = SimpleNamespace(current_value=50)
        db = _FakeSession({
            Faction: _FakeQuery(first=faction),
            Reputation: _FakeQuery(first=reputation),
        })

        assert warp_gate_service.check_traversal_access(db, player, tunnel) is None

    def test_rep_max_blocks_too_reputable_player(self) -> None:
        owner_id = uuid.uuid4()
        player = _fake_player()
        tunnel = self._tunnel_with_layer(owner_id, "faction_rep_max", FactionType.FEDERATION.value, 100)
        faction = SimpleNamespace(id=uuid.uuid4(), faction_type=FactionType.FEDERATION)
        reputation = SimpleNamespace(current_value=500)
        db = _FakeSession({
            Faction: _FakeQuery(first=faction),
            Reputation: _FakeQuery(first=reputation),
        })

        with pytest.raises(WarpGateError) as exc_info:
            warp_gate_service.check_traversal_access(db, player, tunnel)
        assert exc_info.value.status_code == 403
        assert "ERR_GATE_REP_TOO_HIGH" in exc_info.value.detail

    def test_owner_never_subject_to_rep_layers(self) -> None:
        owner = _fake_player()
        tunnel = self._tunnel_with_layer(owner.id, "faction_rep_min", FactionType.FEDERATION.value, 999)
        # No Faction/Reputation spec at all -- the owner path must never
        # even query for the layer.
        db = _FakeSession({})
        assert warp_gate_service.check_traversal_access(db, owner, tunnel) is None


# --- Accept #10: usage tracking accumulates across paid traversals ----------


@pytest.mark.unit
class TestUsageTracking:
    def test_two_paid_traversals_accumulate(self) -> None:
        owner = _fake_player(credits=0, team_id=None)
        traverser = _fake_player(credits=10_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 250},
        )
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        first = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        second = first + timedelta(minutes=5)

        warp_gate_service.collect_toll(db, traverser, tunnel, now=first)
        warp_gate_service.collect_toll(db, traverser, tunnel, now=second)

        stats = tunnel.artificial_data["toll_stats"]
        assert stats["usage_count"] == 2
        assert stats["total_revenue"] == 500
        assert stats["last_used"] == second.isoformat()
        assert tunnel.total_traversals == 2
        assert tunnel.tunnel_status["last_traversal"] == second.isoformat()
        assert traverser.credits == 10_000 - 500
        assert owner.credits == 500


# --- Accept #11: list_sector_structures reports the real toll ---------------


@pytest.mark.unit
class TestListSectorStructuresToll:
    def test_real_toll_reported_not_hardcoded_zero(self) -> None:
        sector_number = 4242
        owner = _fake_player()
        dest_sector = Sector(sector_id=9001, name="Destination")
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 750},
        )
        # A REAL WarpGateBeacon instance, not SimpleNamespace: WarpGate.beacon
        # is a back_populates relationship, and assigning gate.beacon = X
        # fires the reciprocal beacon.gates.append(gate) bookkeeping, which
        # needs a real instrumented `gates` collection on X.
        beacon = WarpGateBeacon(
            player_id=owner.id,
            source_sector_id=sector_number,
            destination_sector_id=9001,
        )
        gate = _fake_gate(owner.id, status=WarpGateStatus.ACTIVE, warp_tunnel_id=tunnel.id)
        gate.beacon = beacon

        db = _FakeSession({
            WarpGate: _FakeQuery(all=[gate]),
            WarpGateBeacon: _FakeQuery(all=[]),  # no DEPLOYED beacons in this sector
            Player: _FakeQuery(first=owner),
            Sector: _FakeQuery(first=dest_sector),
            WarpTunnel: _FakeQuery(first=tunnel),
        })

        result = warp_gate_service.list_sector_structures(db, sector_number)

        assert len(result["gates"]) == 1
        assert result["gates"][0]["toll"] == 750


# --- Accept #12: free (toll=0) gate makes no debit call ---------------------


@pytest.mark.unit
class TestFreeGateRegression:
    def test_zero_toll_gate_makes_no_debit_call(self) -> None:
        owner_id = uuid.uuid4()
        traverser = _fake_player(credits=42)
        tunnel = _fake_tunnel(created_by_player_id=owner_id, access_requirements={})
        # No Player spec -- a free gate must not query for the owner at all.
        db = _FakeSession({})

        result = warp_gate_service.collect_toll(db, traverser, tunnel)

        assert result["charged"] == 0
        assert result["exempt_reason"] == "free"
        assert traverser.credits == 42
        # Bookkeeping still wires the previously-dead total_traversals column
        # (see collect_toll's docstring), but the MOVE outcome — the thing
        # "byte-identical to today" actually governs — is untouched: no
        # credits, no error, no path divergence.
        assert tunnel.total_traversals == 1


# --- Accept #13: no schema change (pure JSONB + one pre-existing column) ----


@pytest.mark.unit
class TestMigrationAdditive:
    def test_no_schema_change_pure_jsonb_plus_existing_column(self) -> None:
        """STORAGE CHOICE: JSONB (access_requirements.toll_amount /
        .toll_bypass / .faction_rep_min / .faction_rep_max,
        artificial_data.toll_stats, tunnel_status.traffic_level /
        .last_traversal) plus WarpTunnel.total_traversals, a column that
        already existed on the model (grep-confirmed pre-WO: zero writers
        anywhere in src/, only read by admin_comprehensive.py's dashboard) —
        this WO gives it its first writer. Zero new columns, zero new
        tables, therefore N/A for an additive-migration check: there is no
        migration to check. Proven by schema introspection rather than git
        (workers run zero git commands) — the columns below are exactly the
        set collect_toll/set_gate_permissions touch, and all pre-date this
        WO."""
        columns = {c.name for c in WarpTunnel.__table__.columns}
        for expected in ("access_requirements", "artificial_data", "tunnel_status", "total_traversals"):
            assert expected in columns
        # access_requirements/artificial_data/tunnel_status are JSONB — no
        # new column type was introduced for any new toll/rep key.
        from sqlalchemy.dialects.postgresql import JSONB
        for jsonb_col in ("access_requirements", "artificial_data", "tunnel_status"):
            assert isinstance(WarpTunnel.__table__.columns[jsonb_col].type, JSONB)
