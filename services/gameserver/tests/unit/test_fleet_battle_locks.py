"""WO-FLEET-BATTLE-LOCKS — the fleet-battle lifecycle NEVER row-locked the
Fleet rows: ``initiate_battle`` read both fleets UNLOCKED
(``self.db.query(Fleet)...first()``), checked in-battle/sector/supply, then
mutated ``attacker.status``/``defender.status`` = IN_BATTLE with no lock at
all — two near-simultaneous ``initiate_battle`` calls sharing one fleet
(double-click / two attackers) could both pass the unlocked IN_BATTLE check
and double-enroll it. This file proves the fix:

  1. ``_lock_fleets_ascending`` — the new shared ascending-id lock helper
     BOTH ``initiate_battle`` and ``simulate_battle_round`` now route
     through (mirrors ``_lock_players_ascending``'s proven N-row pattern,
     WO-FLEET-KILL-LOCK-ORDER / WO-COMBAT-DUAL-LOCK-ORDER): ascending order
     regardless of input order, missing-fleet-id skipped not kept as None.
  2. Role-reversal convergence for a shared fleet PAIR — the deadlock-
     specific proof: two initiate_battle calls with attacker/defender roles
     swapped over the SAME pair converge on the identical acquisition
     order.
  3. The explicit self-attack guard fires BEFORE the lock (zero Fleet
     queries at all for a same-id call).
  4. The state-machine half of "exactly one battle enrolls" achievable
     single-threaded: once the first call's status mutation commits, a
     second call sharing either fleet reads the FRESH (not stale) status
     under its own lock and is rejected. True concurrent-blocking needs
     live Postgres — the orchestrator's leg; this proves the necessary
     post-condition the lock exists to guarantee.
  5. Revert-probe: a structural pin confirming ``_lock_fleets_ascending``
     chains ``.populate_existing()`` before ``.with_for_update()`` (mirrors
     ``test_fleet_casualty_succession.py``'s
     ``TestSimulateBattleRoundPopulateExistingGuard`` pin for the sibling
     FleetBattle lock) — the reason THIS matters is the SAME generic
     SQLAlchemy identity-map mechanics already proven with a real engine in
     that file's ``test_real_sqlalchemy_identity_map_staleness_repro_and_
     fix`` (that file's own docstring: "pure SQLAlchemy identity-map/
     Session mechanics, independent of which mapped class or columns are
     involved") — not re-derived here to avoid duplicating an
     already-established generic proof.
  6. Both call sites (``initiate_battle``, ``simulate_battle_round``)
     actually route through the shared helper, not a separately hand-rolled
     ascending lock each.

DB-free: a small id-keyed fake session records ("lock", fleet_id) events in
acquisition order (same idiom as ``test_fleet_kill_lock_order.py``'s
``_FakeQuery``/``_FakeSession`` for Player).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.fleet import FleetStatus
from src.services.fleet_service import FleetService

SECTOR = uuid4()


def make_fleet_ns(
    *,
    fleet_id=None,
    team_id=None,
    status=FleetStatus.READY.value,
    sector_id=None,
    supply_level=100,
    total_ships=3,
):
    return SimpleNamespace(
        id=fleet_id or uuid4(),
        team_id=team_id,
        status=status,
        sector_id=sector_id if sector_id is not None else SECTOR,
        supply_level=supply_level,
        total_ships=total_ships,
    )


class _FakeFleetQuery:
    """Routes a ``Fleet.id == <literal>`` filter to the matching seeded row
    and appends a ``("lock", id)`` event, in call order, to the shared
    ordered ``events`` list — same idiom as
    ``test_fleet_kill_lock_order.py``'s ``_FakeQuery`` for Player."""

    def __init__(self, fleets, events):
        self._fleets = fleets
        self._events = events
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._match_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        if self._match_id is not None:
            self._events.append(("lock", self._match_id))
        return self

    def first(self):
        return self._fleets.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Fleet store; records lock acquisitions in ``events``.
    ``initiate_battle`` never queries any OTHER model (it constructs
    ``FleetBattle`` directly and ``add()``s it), so this fake need not
    model-dispatch."""

    def __init__(self, *fleets):
        self._fleets = {f.id: f for f in fleets}
        self.events: list = []
        self.added: list = []
        self.commit_count = 0

    @property
    def fleet_lock_log(self):
        return [e[1] for e in self.events if e[0] == "lock"]

    def query(self, model):
        return _FakeFleetQuery(self._fleets, self.events)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1

    def refresh(self, obj):
        pass


# --------------------------------------------------------------------------- #
# 1. Helper unit coverage — _lock_fleets_ascending in isolation.
# --------------------------------------------------------------------------- #

class TestLockFleetsAscendingHelper:
    def test_locks_in_ascending_order_regardless_of_input_order(self):
        a, b, c = sorted(uuid4() for _ in range(3))
        fleets = [make_fleet_ns(fleet_id=fid) for fid in (a, b, c)]
        session = _FakeSession(*fleets)
        service = FleetService(session)

        locked = service._lock_fleets_ascending({c, a, b})

        assert session.fleet_lock_log == [a, b, c]
        assert set(locked.keys()) == {a, b, c}

    def test_missing_fleet_id_is_skipped_not_kept_as_none(self):
        fid = uuid4()
        missing_id = uuid4()
        session = _FakeSession(make_fleet_ns(fleet_id=fid))
        service = FleetService(session)

        locked = service._lock_fleets_ascending({fid, missing_id})

        assert fid in locked
        assert missing_id not in locked


# --------------------------------------------------------------------------- #
# 2. Role-reversal pair — the deadlock-specific proof, through the REAL
#    initiate_battle (not the helper directly), so it also proves the
#    guard/check ordering around the lock is unaffected by role.
# --------------------------------------------------------------------------- #

class TestRoleReversalConverges:
    def test_attacker_low_defender_high(self):
        low_id, high_id = sorted([uuid4(), uuid4()])
        low = make_fleet_ns(fleet_id=low_id, team_id=uuid4())
        high = make_fleet_ns(fleet_id=high_id, team_id=uuid4())
        session = _FakeSession(low, high)
        service = FleetService(session)

        service.initiate_battle(low_id, high_id)

        assert session.fleet_lock_log == [low_id, high_id]

    def test_attacker_high_defender_low_same_pair_reversed_roles(self):
        """The role-reversal that would deadlock against the case above if
        locking simply went "attacker-first, then defender" — a concurrent
        initiate_battle on the SAME pair of fleets, with attacker/defender
        roles swapped, would otherwise lock high-then-low."""
        low_id, high_id = sorted([uuid4(), uuid4()])
        low = make_fleet_ns(fleet_id=low_id, team_id=uuid4())
        high = make_fleet_ns(fleet_id=high_id, team_id=uuid4())
        session = _FakeSession(low, high)
        service = FleetService(session)

        service.initiate_battle(high_id, low_id)

        # Converges on the IDENTICAL ascending order as the reversed-role
        # case above — the structural property that makes two concurrent
        # initiate_battle calls sharing a pair mutually deadlock-safe.
        assert session.fleet_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# 3. Self-attack guard — fires BEFORE the lock (mack LOW: the friendly-fire
#    team_id check only accidentally blocked this).
# --------------------------------------------------------------------------- #

class TestSelfAttackGuard:
    def test_same_fleet_id_raises_before_any_lock(self):
        fid = uuid4()
        fleet = make_fleet_ns(fleet_id=fid, team_id=None)
        session = _FakeSession(fleet)
        service = FleetService(session)

        with pytest.raises(ValueError, match="cannot initiate battle against itself"):
            service.initiate_battle(fid, fid)

        # The guard precedes _lock_fleets_ascending entirely -- zero Fleet
        # queries, zero lock acquisitions.
        assert session.fleet_lock_log == []
        assert session.commit_count == 0

    def test_same_id_self_attack_bypasses_the_team_id_none_gap(self):
        """The gap this guard closes: a teamless fleet (team_id=None) would
        otherwise slip past the friendly-fire check
        (`attacker.team_id is not None and ...` is False when team_id is
        None) and be allowed to battle itself. Proven here directly against
        a team_id=None fleet -- the explicit guard blocks it regardless."""
        fid = uuid4()
        fleet = make_fleet_ns(fleet_id=fid, team_id=None)
        session = _FakeSession(fleet)
        service = FleetService(session)

        with pytest.raises(ValueError, match="cannot initiate battle against itself"):
            service.initiate_battle(fid, fid)


# --------------------------------------------------------------------------- #
# 4. Sequential double-enroll rejected — the state-machine half of "exactly
#    one battle enrolls" achievable without live Postgres. True concurrent
#    blocking (transaction 2 waits for transaction 1's commit before its
#    lock is granted) is the orchestrator's live-PG leg; what's provable
#    here is the POST-CONDITION the lock exists to guarantee: once the
#    first call's status mutation is committed, a second attempt sharing
#    either fleet is rejected under its OWN lock, not silently allowed
#    through on a stale read.
# --------------------------------------------------------------------------- #

class TestSequentialDoubleEnrollRejected:
    def test_second_call_sharing_the_attacker_fleet_is_rejected(self):
        shared_id, first_defender_id, second_defender_id = sorted(
            uuid4() for _ in range(3)
        )
        shared = make_fleet_ns(fleet_id=shared_id, team_id=uuid4())
        first_defender = make_fleet_ns(fleet_id=first_defender_id, team_id=uuid4())
        second_defender = make_fleet_ns(fleet_id=second_defender_id, team_id=uuid4())
        session = _FakeSession(shared, first_defender, second_defender)
        service = FleetService(session)

        battle = service.initiate_battle(shared.id, first_defender.id)

        assert battle.attacker_fleet_id == shared.id
        assert shared.status == FleetStatus.IN_BATTLE.value

        with pytest.raises(ValueError, match="Attacker fleet is already in battle"):
            service.initiate_battle(shared.id, second_defender.id)

    def test_second_call_sharing_the_defender_fleet_is_rejected(self):
        """Symmetric: a fleet already enrolled as a DEFENDER also rejects a
        second attempt that targets it as the new attacker's defender."""
        shared_id, first_attacker_id, second_attacker_id = sorted(
            uuid4() for _ in range(3)
        )
        shared = make_fleet_ns(fleet_id=shared_id, team_id=uuid4())
        first_attacker = make_fleet_ns(fleet_id=first_attacker_id, team_id=uuid4())
        second_attacker = make_fleet_ns(fleet_id=second_attacker_id, team_id=uuid4())
        session = _FakeSession(shared, first_attacker, second_attacker)
        service = FleetService(session)

        service.initiate_battle(first_attacker.id, shared.id)
        assert shared.status == FleetStatus.IN_BATTLE.value

        with pytest.raises(ValueError, match="Defender fleet is already in battle"):
            service.initiate_battle(second_attacker.id, shared.id)


# --------------------------------------------------------------------------- #
# 5. Revert-probe — structural pin. See module docstring point 5 for why the
#    generic SQLAlchemy mechanics proof isn't re-derived in this file.
# --------------------------------------------------------------------------- #

class TestPopulateExistingOrderingPin:
    def test_lock_fleets_ascending_chains_populate_existing_before_with_for_update(self):
        source = inspect.getsource(FleetService._lock_fleets_ascending)
        assert ".populate_existing().with_for_update()" in source, (
            "_lock_fleets_ascending no longer chains .populate_existing() "
            "immediately before .with_for_update() -- this reopens the "
            "stale-identity-map hole documented in the helper's own "
            "docstring: initiate_battle/simulate_battle_round are both "
            "entered after their route already read one or both Fleet rows "
            "UNLOCKED on the same session, so the lock would silently hand "
            "back the pre-lock stale Python object instead of a refreshed "
            "one."
        )


# --------------------------------------------------------------------------- #
# 6. Both call sites route through the shared helper (not a separately
#    hand-rolled ascending lock each -- the whole point of extracting it).
# --------------------------------------------------------------------------- #

class TestBothCallSitesRouteThroughTheSharedHelper:
    def test_initiate_battle_calls_the_shared_helper(self):
        source = inspect.getsource(FleetService.initiate_battle)
        assert "self._lock_fleets_ascending(" in source

    def test_simulate_battle_round_calls_the_shared_helper(self):
        source = inspect.getsource(FleetService.simulate_battle_round)
        assert "self._lock_fleets_ascending(" in source
