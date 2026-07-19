"""WO-MONEY-STRAGGLER-FLUSHFIRST -- combat_service.attack_warp_gate's
salvage-grant Ship lock (`if destroyed:` block, ~:2513) now flushes any
pending in-memory Ship.combat mutation BEFORE its locked
`.populate_existing().with_for_update()` re-read, closing a lost-update.

The mechanism: `_resolve_warp_gate_combat`'s turret-return-fire branch
(structurally unreachable today -- WarpGate has no `turret_count` column,
`getattr(gate, 'turret_count', 0)` is always 0 for every real gate -- but
latent for the day an Upgrades WO adds one, per the assigning WO's own
framing) mutates `attacker.current_ship.combat` in place and
`flag_modified()`s it, UNFLUSHED (the app session is opened
`autoflush=False`, core/database.py:19). `attacker.current_ship` IS the SAME
identity-mapped object the salvage-lock's later
`self.db.query(Ship).filter(...).populate_existing().with_for_update().
first()` resolves to. Before this fix, that `populate_existing()` call would
re-hydrate `combat` from the row's last-FLUSHED (still pre-battle) DB state,
silently discarding the pending turret-damage mutation -- a genuine
lost-update. This is only reachable via a REAL SQLAlchemy identity map -- a
hand-rolled `_FakeSession` (the shape `test_warp_gate_destruction.py`'s own
suite uses for the rest of this method) has no identity map at all and
cannot distinguish "flushed first" from "not" (same limitation
`test_money_nolock_rmw_mack.py` / `test_bounty_collect_flush_populate_
existing.py`'s own docstrings call out for their sites).

HYBRID real-SQLAlchemy session: `Ship.combat` is a Postgres JSONB column, so
the Ship leg of this method runs against a REAL SQLite sub-session
(StaticPool + check_same_thread=False, autoflush=False -- mirrors
core/database.py:19 exactly) via a `MirrorShip` carrying only the columns
`_resolve_warp_gate_combat` / `_calculate_attack_power` / `_ensure_combat_
state` / `maintenance_service.combat_multiplier` actually touch.
`combat_service.Ship` is monkeypatched to `MirrorShip` (module-level import,
combat_service.py:14) so the REAL query shape at the salvage-lock resolves
against it. Every OTHER entity `attack_warp_gate` touches (Player/WarpGate/
WarpGateBeacon/WarpTunnel) stays on the SAME DB-free FakeSession shape
`test_warp_gate_destruction.py`'s own suite already uses -- this WO's fix
touches only the Ship leg, none of those locks -- combined into one
`_HybridSession` so the REAL, unmodified `attack_warp_gate` method runs
end-to-end, not a hand-copied guess at its query shape.

`gate.turret_count` is forced via plain attribute assignment on the
in-memory `WarpGate` ORM instance (matches `test_warp_gate_destruction.py`'s
own `TestTurretReturnFire` precedent) -- there is no backing column, so this
resolves through the resolver's own `getattr(gate, 'turret_count', 0)` hook,
same as a future Upgrades WO's real column would.

LIVE-PROOF-ONLY BOUNDARY: `.with_for_update()` is a documented no-op on
SQLite (same convention as `test_money_nolock_rmw_mack.py` /
`test_bounty_collect_flush_populate_existing.py`) -- this file is entirely
about flush-ordering + identity-map refresh semantics, not real lock
acquisition (that's the orchestrator's live-Postgres leg, same framing as
every other genuine-contention property in this codebase's other
real-SQLAlchemy regression files).
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import StaticPool

from src.models.player import Player
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateBeaconStatus, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import combat_service


# --------------------------------------------------------------------------- #
# Ship leg: REAL SQLAlchemy (SQLite) -- the only entity this WO's fix
# touches, and the only one whose identity-map refresh semantics matter here.
# --------------------------------------------------------------------------- #

def _ship_schema():
    Base = declarative_base()

    class MirrorShip(Base):
        __tablename__ = "mirror_ships_gate_salvage"
        id = sa.Column(sa.Integer, primary_key=True)
        owner_id = sa.Column(sa.Integer, nullable=False)
        type = sa.Column(sa.String, nullable=True)
        is_destroyed = sa.Column(sa.Boolean, nullable=False, default=False)
        combat = sa.Column(sa.JSON, nullable=True)
        cargo = sa.Column(sa.JSON, nullable=True)
        maintenance = sa.Column(sa.JSON, nullable=True)

    return Base, MirrorShip


def _ship_session_factory(Base) -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) -- the entire bug this WO fixes only exists
    # because autoflush is off.
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# --------------------------------------------------------------------------- #
# Everything else: DB-free FakeSession, same WHERE-clause interpreter
# convention as test_warp_gate_destruction.py (this WO's fix touches none of
# these locks).
# --------------------------------------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        right = cond.right.value if hasattr(cond.right, "value") else cond.right
        return row_val == right
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None,
        session: Optional["_HybridSession"] = None, entity: Optional[str] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions), self._session, self._entity)

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()


class _HybridSession:
    """Player/WarpGate/WarpGateBeacon/WarpTunnel routed to a DB-free
    FakeQuery; Ship routed to a REAL SQLAlchemy sub-session (`ship_session`)
    -- the ONE entity this WO's fix touches."""

    def __init__(
        self, *, ship_session, ship_model, players=(), gates=(), beacons=(), tunnels=(),
    ) -> None:
        self.ship_session = ship_session
        self._ship_model = ship_model
        self.players = list(players)
        self.gates = list(gates)
        self.beacons = list(beacons)
        self.tunnels = list(tunnels)
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.for_update_calls: List[Optional[str]] = []

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is self._ship_model:
            return self.ship_session.query(head)
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is WarpGate:
            return _FakeQuery(self.gates, session=self, entity="WarpGate")
        if head is WarpGateBeacon:
            return _FakeQuery(self.beacons, session=self, entity="WarpGateBeacon")
        if head is WarpTunnel:
            return _FakeQuery(self.tunnels, session=self, entity="WarpTunnel")
        return _FakeQuery([])

    def add(self, obj: Any) -> None:
        pass

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)
        if obj in self.beacons:
            self.beacons.remove(obj)
        if obj in self.gates:
            self.gates.remove(obj)

    def flush(self) -> None:
        self.flush_calls += 1
        self.ship_session.flush()

    def commit(self) -> None:
        self.commit_calls += 1
        self.ship_session.commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        self.ship_session.rollback()


# --- fixtures -------------------------------------------------------------- #

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freezes combat_service.py's OWN `datetime` reference to `_NOW` --
    same rationale/shape as test_warp_gate_destruction.py's own fixture:
    the invulnerability-window check calls datetime.now(timezone.utc)
    directly, so without this every fixture beacon is date-lucky against
    real wall-clock time."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _NOW if tz is not None else _NOW.replace(tzinfo=None)

    monkeypatch.setattr(combat_service, "datetime", _FrozenDatetime)


def _setup(monkeypatch: pytest.MonkeyPatch, *, seed_shields: float = 100.0):
    """Builds one attacker/gate/beacon/tunnel/ship fixture set, wired
    through a fresh _HybridSession. Returns everything a test needs to
    drive `attack_warp_gate` and inspect the Ship's post-call state."""
    Base, MirrorShip = _ship_schema()
    ShipSessionFactory = _ship_session_factory(Base)
    monkeypatch.setattr(combat_service, "Ship", MirrorShip)

    seed = ShipSessionFactory()
    seed.add(MirrorShip(
        id=1, owner_id=1, type=None, is_destroyed=False,
        combat={
            "shields": seed_shields, "max_shields": 100.0,
            "hull": 200.0, "max_hull": 200.0,
            "shield_recharge_rate": 5.0,
        },
        cargo={"capacity": 500, "used": 0, "contents": {}},
        maintenance=None,
    ))
    seed.commit()
    seed.close()

    ship_session = ShipSessionFactory()
    ship = ship_session.query(MirrorShip).filter(MirrorShip.id == 1).first()

    attacker = SimpleNamespace(
        id=1, username="Voyager7", current_sector_id=99,
        current_ship_id=1, current_ship=ship,
        turns=1000, max_turns=1000, is_docked=False, is_landed=False,
        attack_drones=0, military_rank=None,
        last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )

    beacon = WarpGateBeacon(
        id=uuid.uuid4(), player_id=uuid.uuid4(),
        source_sector_id=42, destination_sector_id=99,
        status=WarpGateBeaconStatus.MATCHED,
        invulnerable_until=None, hp=5000,
        created_at=_NOW - timedelta(hours=72),  # well past the 48h window
    )
    gate = WarpGate(
        id=uuid.uuid4(), beacon_id=beacon.id, player_id=beacon.player_id,
        warp_tunnel_id=uuid.uuid4(), status=WarpGateStatus.ACTIVE,
        hp=1,  # guaranteed one-pass kill -> the destroyed branch runs
        harmonization_completes_at=None, anchor_ship_id=None,
        construction_cost=0,
    )
    gate.turret_count = 1  # NO-CANON hook, no backing column -- see module docstring
    tunnel = WarpTunnel(
        id=gate.warp_tunnel_id, name="Test Tunnel",
        origin_sector_id=uuid.uuid4(), destination_sector_id=uuid.uuid4(),
        type=WarpTunnelType.ARTIFICIAL, status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=False,
    )

    db = _HybridSession(
        ship_session=ship_session, ship_model=MirrorShip,
        players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel],
    )
    return db, attacker, ship, gate, beacon, tunnel


# --- THE DANGER, isolated ---------------------------------------------------- #

@pytest.mark.unit
class TestPopulateExistingWithoutPriorFlushDiscardsThePendingMutation:
    def test_direct_populate_existing_without_a_flush_discards_the_pending_shield_mutation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mirrors test_bounty_collect_flush_populate_existing.py's own
        precedent: call the salvage lock's EXACT query shape directly,
        WITHOUT the flush this WO's fix adds immediately before it, after
        an in-memory-only combat mutation on the SAME identity-mapped Ship
        -- proves WHY the flush has to be there, and that this harness can
        actually detect the regression it guards against (not test luck)."""
        db, attacker, ship, gate, beacon, tunnel = _setup(monkeypatch)
        pre_battle_shields = ship.combat["shields"]
        assert pre_battle_shields == 100.0

        # Simulate the turret-return-fire mutation in-memory, unflushed --
        # the SAME shape _resolve_warp_gate_combat's turret branch performs
        # (combat_service.py ~:2661-2669): mutate the dict in place and
        # flag_modified it, never flush.
        ship.combat["shields"] = 42.0
        flag_modified(ship, "combat")

        # NO flush() here -- the counterfactual: the salvage lock's query
        # shape WITHOUT this WO's fix.
        reread = (
            db.query(combat_service.Ship)
            .filter(
                combat_service.Ship.id == attacker.current_ship_id,
                combat_service.Ship.owner_id == attacker.id,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )

        assert reread is ship  # same identity-mapped object, refreshed in place
        assert reread.combat["shields"] == pre_battle_shields == 100.0, (
            "harness sanity check failed -- without a prior flush(), "
            "populate_existing() must re-hydrate 'combat' from the DB's "
            "last-FLUSHED (pre-mutation) state, discarding the pending "
            "42.0 shield value. If this ever reads 42.0, this harness can "
            "no longer detect the lost-update this WO's flush() closes."
        )


# --- THE FIX, end-to-end through the real public method --------------------- #

@pytest.mark.unit
class TestAttackWarpGateFlushFirstPreservesReturnFire:
    def test_turret_return_fire_survives_the_salvage_locks_populate_existing_reread(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db, attacker, ship, gate, beacon, tunnel = _setup(monkeypatch)
        pre_battle_shields = ship.combat["shields"]

        captured: dict = {}
        real_resolve = combat_service.CombatService._resolve_warp_gate_combat

        def _spy_resolve(self, attacker_arg, gate_arg):
            res = real_resolve(self, attacker_arg, gate_arg)
            captured["result"] = res
            return res

        monkeypatch.setattr(combat_service.CombatService, "_resolve_warp_gate_combat", _spy_resolve)

        result = combat_service.CombatService(db).attack_warp_gate(
            attacker_id=attacker.id, gate_id=gate.id,
        )

        assert result["success"] is True
        assert result["destroyed"] is True

        return_fire = captured["result"]["return_fire"]
        assert return_fire is not None, "turret_count=1 must have produced a return-fire hit"
        assert return_fire["shield_damage"] > 0, (
            "harness sanity check -- the turret hit must actually have "
            "computed positive shield damage for this test to mean anything"
        )

        # THE FIX: the salvage lock's populate_existing() re-read must see
        # the POST-return-fire value, not the pre-battle seed -- proving
        # this WO's self.db.flush() (immediately before the query) landed
        # the pending mutation before the re-read, rather than discarding
        # it (see TestPopulateExistingWithoutPriorFlushDiscardsThePending
        # Mutation above for what happens without it).
        assert ship.combat["shields"] == pytest.approx(
            pre_battle_shields - return_fire["shield_damage"]
        )
        assert ship.combat["shields"] < pre_battle_shields
        assert result["salvage_granted"] == combat_service.CombatService.GATE_SALVAGE_YIELD

    def test_salvage_lock_flushed_and_locked_the_ship_row(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The salvage Ship query still acquires FOR UPDATE (belt +
        suspenders on top of the flush-ordering fix -- neither regresses
        the other)."""
        db, attacker, ship, gate, beacon, tunnel = _setup(monkeypatch)

        combat_service.CombatService(db).attack_warp_gate(
            attacker_id=attacker.id, gate_id=gate.id,
        )

        # The real ship_session's own query executed with FOR UPDATE
        # requested is a documented SQLite no-op (see module docstring);
        # what's provable DB-free-side is that the flush ran at least once
        # (our fix) before commit.
        assert db.flush_calls >= 1
        assert db.commit_calls == 1


# --- source-level ordering pin ----------------------------------------------- #

@pytest.mark.unit
class TestFlushPrecedesSalvageLockInSource:
    def test_flush_call_immediately_precedes_the_salvage_ship_query(self) -> None:
        """Source-level regression pin (matches test_warp_gate_destruction.
        py's own TestGateHpDefaults/TestEmitUsesGateIdParameter precedent):
        pins the ORDERING invariant a runtime assertion alone can't
        distinguish from luck once the flush becomes a no-op (e.g. nothing
        pending) -- self.db.flush() must appear exactly once in the
        salvage-lock region, before the populate_existing() call."""
        source = inspect.getsource(combat_service.CombatService.attack_warp_gate)

        # Isolate the destroyed-branch salvage-lock region (starts at the
        # cargo-refund import, ends at the granted-salvage dict copy) so a
        # flush() elsewhere in the method (e.g. the later beacon-delete
        # flush) can't accidentally satisfy this check.
        start = source.index("from src.services.warp_gate_service import _refund_cargo")
        end = source.index("salvage_granted = dict(self.GATE_SALVAGE_YIELD)")
        region = source[start:end]

        assert region.count("self.db.flush()") == 1, (
            "expected exactly one flush() in the salvage-lock region -- "
            "if this fires, the flush-before-populate_existing ordering "
            "this WO's fix relies on may have been removed or duplicated"
        )
        assert ".query(Ship)" in region, "expected exactly one Ship query in the salvage-lock region"

        # Anchored on `.query(Ship)` rather than the first `.populate_
        # existing()` match -- this fix's own explanatory comment ABOVE the
        # flush() call mentions ".populate_existing()" in prose, which would
        # otherwise be found first and falsely appear to precede the flush.
        flush_index = region.index("self.db.flush()")
        query_call_index = region.index(".query(Ship)")
        assert flush_index < query_call_index, (
            "self.db.flush() must appear BEFORE the salvage lock's "
            "Ship query, not after"
        )
        # The actual code's populate_existing() call is the one following
        # the query call (as opposed to any earlier comment mention of the
        # same text).
        populate_existing_index = region.index(".populate_existing()", query_call_index)
        assert query_call_index < populate_existing_index < region.index(".with_for_update()", query_call_index)
