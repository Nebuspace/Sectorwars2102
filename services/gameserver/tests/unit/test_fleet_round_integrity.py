"""WO-FLEET-ROUND-INTEGRITY — closes the live team-treasury DOUBLE-PAY
exploit both gates found on WO-FLEET-BATTLE-LOCKS.

The exploit (cipher HIGH): ``simulate_battle_round``'s fire loop hit
``remove_ship_from_fleet``'s own ``self.db.commit()`` on the FIRST casualty,
which RELEASED the FleetBattle + Fleet FOR-UPDATE locks (``expire_on_commit
=True`` expires everything) — the round's TAIL then ran unlocked. A
double-submitted 2nd ``POST /fleets/battles/{id}/simulate-round`` (the route
checks fleet-membership only, no idempotency key) could race the unlocked
tail; worst case BOTH satisfied ``_should_end_battle`` → BOTH called
``_end_battle`` (no ``ended_at`` guard) → the team-treasury loot transfer ran
TWICE → attacker credited twice, defender debited twice.

Three sub-parts proven here:
  (a) ``_end_battle`` is now IDEMPOTENT (``ended_at`` guard, caller-agnostic)
      -> TestEndBattleIdempotent.
  (b) ``remove_ship_from_fleet``'s casualty call is now flush-only
      (``commit=False``), so ``simulate_battle_round`` produces exactly ONE
      commit for the whole round (its own final commit, or ``_end_battle``'s
      if the round also ends the battle); the manual-removal route keeps its
      OWN immediate commit (default ``commit=True``), unchanged
      -> TestRemoveShipFromFleetCommitParameter,
         TestSimulateBattleRoundSingleCommit.
  (c) ``add_ship_to_fleet`` / ``disband_fleet`` now lock the target Fleet row
      via the SAME ``_lock_fleets_ascending`` helper before checking
      ``Fleet.status`` — closing a TOCTOU against ``initiate_battle``'s
      enroll flip -> TestAddShipDisbandToctou.

DB-free: real (transient) ORM instances for Fleet/FleetMember/Ship/
FleetBattle/Team (mirrors test_fleet_casualty_succession.py's precedent that
real ``back_populates`` relationship wiring "just works" without a session).
The shared ``_FakeQuery``/``_FakeSession`` interprets the SUT's REAL
SQLAlchemy filter() conditions against live, mutable in-memory pools per
model class — same established idiom as test_fleet_casualty_succession.py's
own fakes (itself mirroring test_route_runs_retention.py /
test_warp_gate_toll.py), extended here with a bulk ``.delete()`` (needed by
``disband_fleet``'s ``query(FleetMember).filter(...).delete()``) and
separate commit/flush counters (needed to pin the commit-boundary fix).

True concurrent DB-level blocking (a second transaction's SELECT ... FOR
UPDATE genuinely waiting on a live row lock) needs real Postgres — the
orchestrator's leg. What's provable DB-free, per the established precedent
in test_fleet_battle_locks.py (its own docstring, points 4-5): the STATE-
MACHINE post-condition the lock exists to guarantee (once a status mutation
is committed, a lock-routed re-read sees it fresh, not a stale snapshot) and
a STRUCTURAL pin that the fixed methods actually route through the shared
``_lock_fleets_ascending`` helper (not a separately hand-rolled unlocked
query). The generic SQLAlchemy identity-map staleness mechanics themselves
are proven once, with a real engine, in test_fleet_casualty_succession.py —
not re-derived here.
"""
from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from src.models.fleet import (
    Fleet, FleetMember, FleetBattle, FleetRole, FleetStatus, BattlePhase,
)
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.team import Team
from src.services import fleet_service as fs
from src.services.fleet_service import FleetService


# --------------------------------------------------------------------------- #
# Fake DB — interprets the SUT's real filter() conditions against live pools.
# --------------------------------------------------------------------------- #

def _flatten(conditions):
    flat = []
    for c in conditions:
        if hasattr(c, "clauses"):  # and_(...) -> BooleanClauseList
            flat.extend(c.clauses)
        else:
            flat.append(c)
    return flat


def _condition_matches(row, condition):
    return getattr(row, condition.left.key) == condition.right.value


class _FakeQuery:
    def __init__(self, pool: List[Any], session: "_FakeSession"):
        self._pool = pool
        self._session = session
        self._conditions: List[Any] = []

    def filter(self, *conditions):
        self._conditions = self._conditions + _flatten(conditions)
        return self

    def with_for_update(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def _matching(self):
        return [r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)]

    def first(self):
        matches = self._matching()
        return matches[0] if matches else None

    def all(self):
        return list(self._matching())

    def delete(self) -> int:
        """Bulk delete — mirrors disband_fleet's
        ``query(FleetMember).filter(...).delete()``. Removes matches from
        the live pool and returns the count, same as the real API."""
        matches = self._matching()
        for row in matches:
            if row in self._pool:
                self._pool.remove(row)
            if isinstance(row, FleetMember):
                row.fleet = None
        return len(matches)


class _FakeSession:
    """Maps a model class -> its live pool. delete() on a FleetMember
    detaches it from its Fleet via the REAL back_populates relationship
    (mirrors test_fleet_casualty_succession.py's _FakeSession). Tracks
    commit/flush counts SEPARATELY so the commit-boundary fix (WO-FLEET-
    ROUND-INTEGRITY sub-part (b)) can be pinned directly."""

    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.commit_count = 0
        self.flush_count = 0

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []), self)

    def add(self, obj):
        self.added.append(obj)
        self._pools.setdefault(type(obj), []).append(obj)

    def delete(self, obj):
        self.deleted.append(obj)
        pool = self._pools.get(type(obj))
        if pool is not None and obj in pool:
            pool.remove(obj)
        if isinstance(obj, FleetMember):
            obj.fleet = None

    def commit(self):
        self.commit_count += 1

    def refresh(self, obj):
        pass

    def flush(self):
        self.flush_count += 1


# --------------------------------------------------------------------------- #
# Fixtures — real (transient) ORM instances
# --------------------------------------------------------------------------- #

def make_team(*, treasury_credits=0):
    return Team(id=uuid4(), name=f"team-{uuid4().hex[:8]}", treasury_credits=treasury_credits)


def make_fleet(*, team=None, status=FleetStatus.IN_BATTLE.value, commander_id=None):
    fleet = Fleet(
        id=uuid4(),
        team_id=team.id if team else uuid4(),
        commander_id=commander_id,
        name="Test Fleet",
        status=status,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
    )
    fleet.team = team
    return fleet


def make_ship(*, hull=100, max_hull=100, shields=0, attack_rating=10, name="Ship", owner_id=None):
    return Ship(
        id=uuid4(),
        name=name,
        type=ShipType.CARRIER,
        owner_id=owner_id if owner_id is not None else uuid4(),
        sector_id=1,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        maintenance={},
        cargo={},
        combat={"hull": hull, "max_hull": max_hull, "shields": shields, "attack_rating": attack_rating},
    )


def make_member(*, fleet, ship, role=FleetRole.ATTACKER):
    member = FleetMember(
        id=uuid4(),
        fleet_id=fleet.id,
        ship_id=ship.id,
        player_id=ship.owner_id,
        role=role.value if hasattr(role, "value") else role,
        joined_at=datetime.utcnow(),
    )
    member.fleet = fleet  # back_populates -> fleet.members
    member.ship = ship
    return member


def make_battle(*, attacker_fleet, defender_fleet, phase=BattlePhase.ENGAGEMENT.value, battle_log=None):
    battle = FleetBattle(
        id=uuid4(),
        attacker_fleet_id=attacker_fleet.id,
        defender_fleet_id=defender_fleet.id,
        started_at=datetime.utcnow(),
        phase=phase,
        battle_log=battle_log if battle_log is not None else [],
        attacker_damage_dealt=0,
        defender_damage_dealt=0,
        total_damage_dealt=0,
    )
    battle.attacker_fleet = attacker_fleet
    battle.defender_fleet = defender_fleet
    return battle


def make_session(pools: Dict[type, List[Any]] | None = None) -> _FakeSession:
    return _FakeSession(pools or {})


# --------------------------------------------------------------------------- #
# (a) _end_battle idempotency — the double-pay fix.
# --------------------------------------------------------------------------- #

class TestEndBattleIdempotent:
    def _decisive_attacker_win_setup(self):
        """Attacker has one live ship; defender has NO members at all (so
        both defender_strength == 0 and _get_active_fleet_ships(defender) ==
        [] hold) -> deterministic battle.winner == "attacker" regardless of
        which winner-determination branch fires first, no strength-ratio
        ambiguity."""
        attacker_team = make_team(treasury_credits=0)
        defender_team = make_team(treasury_credits=1000)
        attacker_fleet = make_fleet(team=attacker_team)
        defender_fleet = make_fleet(team=defender_team)
        a_ship = make_ship(hull=100, max_hull=100)
        make_member(fleet=attacker_fleet, ship=a_ship)  # attaches via back_populates

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        # Team must be seeded into the fake session's pool (WO-FLEET-
        # TREASURY-LOCK): _apply_battle_loot now locks both Team rows via a
        # genuine self.db.query(Team)...with_for_update() re-read instead of
        # the old in-Python `.team` relationship access, so the fake session
        # needs Team rows to resolve that query against.
        session = make_session({Team: [attacker_team, defender_team]})
        svc = FleetService(db=session)
        return svc, session, battle, attacker_team, defender_team

    def test_first_call_transfers_loot_exactly_once(self):
        svc, session, battle, attacker_team, defender_team = self._decisive_attacker_win_setup()

        result = svc._end_battle(battle)

        assert battle.ended_at is not None
        assert battle.winner == "attacker"
        loot = 1000 // 10  # 10% of the loser's pre-battle treasury
        assert defender_team.treasury_credits == 1000 - loot
        assert attacker_team.treasury_credits == 0 + loot
        assert result["credits_looted"] == loot
        assert session.commit_count == 1

    def test_second_call_on_an_already_ended_battle_does_not_repeat_the_transfer(self):
        """Simulates the exploit: a second _end_battle call reaching the
        SAME (now-ended) battle object — exactly what a racing double-
        submitted simulate-round call used to trigger. Post-fix, the second
        call must be a pure no-op: no second treasury mutation, no second
        commit."""
        svc, session, battle, attacker_team, defender_team = self._decisive_attacker_win_setup()

        result1 = svc._end_battle(battle)
        loot = 1000 // 10
        assert defender_team.treasury_credits == 1000 - loot
        assert attacker_team.treasury_credits == loot

        result2 = svc._end_battle(battle)

        # Not double-debited / double-credited -- the exploit this closes.
        assert defender_team.treasury_credits == 1000 - loot
        assert attacker_team.treasury_credits == loot
        # No second commit -- the short-circuit never reaches self.db.commit().
        assert session.commit_count == 1
        # Identical result shape both times (via the shared _battle_end_result).
        assert result2 == result1

    def test_third_and_subsequent_calls_also_stay_inert(self):
        """Not just "twice safe" -- N racing calls must all collapse to the
        same single transfer, matching an arbitrarily-retried double-submit
        client."""
        svc, session, battle, attacker_team, defender_team = self._decisive_attacker_win_setup()

        svc._end_battle(battle)
        loot = 1000 // 10
        for _ in range(5):
            svc._end_battle(battle)

        assert defender_team.treasury_credits == 1000 - loot
        assert attacker_team.treasury_credits == loot
        assert session.commit_count == 1

    def test_already_ended_short_circuit_never_touches_battle_log_or_status(self):
        """The short-circuit must be a TRUE no-op on the battle's own state
        too -- not just the treasury: re-running the winner/status/
        battle-log mutations on an already-ended battle would itself be a
        (non-money) correctness bug."""
        svc, session, battle, attacker_team, defender_team = self._decisive_attacker_win_setup()
        svc._end_battle(battle)

        log_len_after_first = len(battle.battle_log)
        fleet_last_battle = battle.attacker_fleet.last_battle

        svc._end_battle(battle)

        assert len(battle.battle_log) == log_len_after_first
        assert battle.attacker_fleet.last_battle == fleet_last_battle

    def test_already_ended_guard_is_the_first_statement(self):
        """Structural pin: the ended_at check must precede any mutation --
        confirms the guard is a true entry-guard, not a check inserted after
        mutations have already started (which would only be a partial fix)."""
        source = inspect.getsource(FleetService._end_battle)
        # The guard clause must appear before the first assignment to
        # battle.ended_at (the mutation that actually ends the battle).
        guard_idx = source.index("if battle.ended_at is not None")
        mutate_idx = source.index("battle.ended_at = datetime.utcnow()")
        assert guard_idx < mutate_idx


# --------------------------------------------------------------------------- #
# (b) remove_ship_from_fleet's commit parameter + the round's commit boundary.
# --------------------------------------------------------------------------- #

class TestRemoveShipFromFleetCommitParameter:
    def _seeded(self):
        fleet = make_fleet(status=FleetStatus.READY.value)
        ship = make_ship()
        member = make_member(fleet=fleet, ship=ship)
        session = make_session({FleetMember: [member]})
        svc = FleetService(db=session)
        return svc, session, fleet, ship

    def test_default_commit_true_matches_the_manual_removal_route(self):
        """The manual-removal route (fleets.py DELETE .../remove-ship/...)
        calls remove_ship_from_fleet with NO commit kwarg -- its observable
        behavior (an immediate commit, zero flush) must be UNCHANGED."""
        svc, session, fleet, ship = self._seeded()

        result = svc.remove_ship_from_fleet(fleet.id, ship.id)

        assert result is True
        assert session.commit_count == 1
        assert session.flush_count == 0

    def test_explicit_commit_true_is_identical_to_the_default(self):
        svc, session, fleet, ship = self._seeded()

        svc.remove_ship_from_fleet(fleet.id, ship.id, commit=True)

        assert session.commit_count == 1
        assert session.flush_count == 0

    def test_commit_false_flushes_only_never_commits(self):
        """The mid-battle KIA path's shape: flush-only, folding the removal
        into the caller's own (not-yet-committed) transaction."""
        svc, session, fleet, ship = self._seeded()

        result = svc.remove_ship_from_fleet(fleet.id, ship.id, commit=False)

        assert result is True
        assert session.commit_count == 0
        assert session.flush_count == 1

    def test_record_ship_casualty_calls_remove_ship_from_fleet_with_commit_false(self):
        """Structural pin on the ONE call site this WO changes -- the shared
        removal path itself keeps commit=True as its DEFAULT (proven above),
        so the casualty caller must explicitly opt into commit=False."""
        source = inspect.getsource(FleetService._record_ship_casualty)
        assert "self.remove_ship_from_fleet(member.fleet_id, ship.id, commit=False)" in source

    def test_no_other_caller_of_remove_ship_from_fleet_exists_in_src(self):
        """Verify-first pin: remove_ship_from_fleet has exactly TWO callers
        in src/ -- the manual route (fleets.py, via the default commit=True)
        and _record_ship_casualty (commit=False). A THIRD caller added later
        without considering this commit boundary would be easy to miss."""
        import pathlib
        import re

        src_root = pathlib.Path(fs.__file__).resolve().parents[1]  # .../src
        callers = []
        pattern = re.compile(r"\.remove_ship_from_fleet\(")
        for path in src_root.rglob("*.py"):
            text = path.read_text()
            for line in text.splitlines():
                if pattern.search(line) and "def remove_ship_from_fleet" not in line:
                    callers.append(str(path.relative_to(src_root)))
        assert sorted(callers) == sorted([
            "api/routes/fleets.py",       # manual removal route -- default commit=True
            "services/fleet_service.py",  # _record_ship_casualty -- explicit commit=False
        ]), (
            f"remove_ship_from_fleet's caller set (by file) in src/ has "
            f"changed -- found {callers}. A new caller must deliberately "
            f"choose commit=True (immediate, matches the manual route) or "
            f"commit=False (flush-only, matches the mid-battle KIA path) "
            f"rather than silently inheriting the default."
        )


class TestSimulateBattleRoundSingleCommit:
    def test_mid_round_casualty_produces_exactly_one_commit(self, monkeypatch):
        """The core commit-boundary proof: a casualty mid-fire-loop must NOT
        release the round's locks early. Attacker one-shots defender's only
        ship, which also ends the battle this round (defender_ships becomes
        empty) -- so this single test exercises BOTH halves of the fix: the
        mid-round removal is flush-only (no early commit), AND the
        subsequent _end_battle call is the round's ONE and ONLY commit."""
        class _StubShipService:
            def __init__(self, db):
                pass

            def is_ship_indestructible(self, ship):
                return False

            def destroy_ship(self, ship, destroyer=None, cause=None):
                ship.is_destroyed = True

        class _StubCombatService:
            def __init__(self, db):
                pass

            def _spawn_cargo_wreck(self, **kwargs):
                pass

        monkeypatch.setattr("src.services.ship_service.ShipService", _StubShipService)
        monkeypatch.setattr("src.services.combat_service.CombatService", _StubCombatService)

        attacker_team = make_team(treasury_credits=0)
        defender_team = make_team(treasury_credits=500)
        attacker_fleet = make_fleet(team=attacker_team)
        defender_fleet = make_fleet(team=defender_team)
        a_ship = make_ship(hull=1000, max_hull=1000, attack_rating=1000)  # overwhelming
        d_ship = make_ship(hull=1, max_hull=100)  # one-shot fragile
        a_member = make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = make_member(fleet=defender_fleet, ship=d_ship)

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        session = make_session({
            FleetMember: [a_member, d_member],
            FleetBattle: [battle],
            Fleet: [attacker_fleet, defender_fleet],
        })
        svc = FleetService(db=session)
        svc._distribute_fleet_kill_rewards = lambda *a, **k: None  # unrelated machinery, tested elsewhere

        monkeypatch.setattr(fs.random, "random", lambda: 0.1)   # every hit-chance roll succeeds
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        result = svc.simulate_battle_round(battle.id)

        # The battle ended this round (defender wiped) -- confirms the kill
        # path (and therefore the mid-round remove_ship_from_fleet call)
        # actually ran.
        assert result["battle_ongoing"] is False
        assert battle.ended_at is not None
        assert d_member not in session._pools[FleetMember]  # casualty removal did happen

        # THE proof: exactly one commit for the whole round, despite a
        # mid-round casualty. Pre-fix this would be 2 (the casualty's own
        # commit + _end_battle's).
        assert session.commit_count == 1
        # The casualty's removal folded into the round via flush, not commit.
        assert session.flush_count >= 1

    def test_no_casualty_round_still_produces_exactly_one_commit(self, monkeypatch):
        """Baseline: an uneventful round (no deaths) was ALWAYS single-commit
        -- confirms the fix didn't regress the common case."""
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        a_ship = make_ship(hull=1000, max_hull=1000, attack_rating=10)
        d_ship = make_ship(hull=1000, max_hull=1000, attack_rating=10)
        a_member = make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = make_member(fleet=defender_fleet, ship=d_ship)

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        session = make_session({
            FleetMember: [a_member, d_member],
            FleetBattle: [battle],
            Fleet: [attacker_fleet, defender_fleet],
        })
        svc = FleetService(db=session)

        monkeypatch.setattr(fs.random, "random", lambda: 0.1)
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        result = svc.simulate_battle_round(battle.id)

        assert result["battle_ongoing"] is True
        assert session.commit_count == 1
        assert session.flush_count == 0  # nobody died -- no flush-only removal needed


# --------------------------------------------------------------------------- #
# (c) add_ship_to_fleet / disband_fleet TOCTOU vs initiate_battle's enroll.
# --------------------------------------------------------------------------- #

class TestAddShipDisbandToctou:
    def test_add_ship_routes_through_the_shared_lock_helper(self):
        """Structural pin: add_ship_to_fleet must acquire its Fleet lock via
        the SAME _lock_fleets_ascending helper initiate_battle/
        simulate_battle_round use (not a separately hand-rolled, unlocked
        query) -- this is the concrete populate_existing()+with_for_update()
        chain that matters under real concurrency."""
        source = inspect.getsource(FleetService.add_ship_to_fleet)
        assert "self._lock_fleets_ascending(" in source

    def test_disband_fleet_routes_through_the_shared_lock_helper(self):
        source = inspect.getsource(FleetService.disband_fleet)
        assert "self._lock_fleets_ascending(" in source

    def test_add_ship_after_fleet_enters_battle_is_rejected(self):
        """Sequential post-condition (mirrors test_fleet_battle_locks.py's
        TestSequentialDoubleEnrollRejected): once initiate_battle's
        lock-held status mutation is committed, add_ship_to_fleet's OWN
        lock-routed re-read sees the FRESH in_battle status, not a stale
        forming/ready snapshot -- the state-machine guarantee the lock
        exists to provide. True concurrent blocking is the orchestrator's
        live-PG leg (see module docstring)."""
        attacker = make_fleet(status=FleetStatus.READY.value)
        defender = make_fleet(status=FleetStatus.READY.value)
        pilot_ship = make_ship()
        session = make_session({Fleet: [attacker, defender]})
        svc = FleetService(db=session)

        svc.initiate_battle(attacker.id, defender.id)
        assert attacker.status == FleetStatus.IN_BATTLE.value

        # A new ship tries to join the now-battling fleet. The status guard
        # (checked before ship-ownership validation) must reject it before
        # ever reaching the ship lookup -- so pilot_ship's owner needn't be
        # seeded into the fleet's team for this rejection to fire correctly.
        session._pools[Ship] = [pilot_ship]
        with pytest.raises(ValueError, match="Fleet is not accepting new members"):
            svc.add_ship_to_fleet(attacker.id, pilot_ship.id)

    def test_disband_after_fleet_enters_battle_is_rejected(self):
        """Symmetric TOCTOU close for disband_fleet."""
        attacker = make_fleet(status=FleetStatus.READY.value)
        defender = make_fleet(status=FleetStatus.READY.value)
        session = make_session({Fleet: [attacker, defender]})
        svc = FleetService(db=session)

        svc.initiate_battle(attacker.id, defender.id)
        assert defender.status == FleetStatus.IN_BATTLE.value

        with pytest.raises(ValueError, match="Cannot disband fleet during battle"):
            svc.disband_fleet(defender.id)

    def test_add_ship_still_succeeds_normally_when_fleet_is_forming(self):
        """Regression guard: the new lock must not break the ordinary,
        uncontested add-ship path."""
        fleet = make_fleet(status=FleetStatus.FORMING.value)
        ship = make_ship()
        ship.owner = Player(id=ship.owner_id, team_id=fleet.team_id)  # same-team ownership check
        session = make_session({Fleet: [fleet], Ship: [ship], FleetMember: []})
        svc = FleetService(db=session)

        member = svc.add_ship_to_fleet(fleet.id, ship.id)

        # NOTE: does not assert fleet.total_ships here -- _recalculate_fleet_
        # stats reads fleet.members, which only reflects the just-added
        # member under a REAL session's autoflush-triggered relationship
        # reload (fleet is persistent there); this transient, session-free
        # fake fleet has no such reload, so fleet.members stays empty
        # regardless of this fix. That stats-recalc coupling is pre-existing
        # and out of this WO's scope -- what's being proven here is that the
        # NEW lock doesn't block the ordinary path.
        assert member.ship_id == ship.id
        assert member.fleet_id == fleet.id
        assert session.commit_count == 1

    def test_disband_still_succeeds_normally_when_fleet_is_ready(self):
        """Regression guard, symmetric to the add-ship one above."""
        fleet = make_fleet(status=FleetStatus.READY.value)
        session = make_session({Fleet: [fleet], FleetMember: []})
        svc = FleetService(db=session)

        result = svc.disband_fleet(fleet.id)

        assert result is True
        assert fleet.status == FleetStatus.DISBANDED.value

    def test_add_ship_missing_fleet_still_raises_not_found(self):
        """Regression guard: the lock helper's "missing id -> skipped, not
        kept as None" behavior must still surface the SAME not-found error
        add_ship_to_fleet raised before this change."""
        session = make_session({Fleet: []})
        svc = FleetService(db=session)

        with pytest.raises(ValueError, match="not found"):
            svc.add_ship_to_fleet(uuid4(), uuid4())

    def test_disband_missing_fleet_still_returns_false_not_raises(self):
        session = make_session({Fleet: []})
        svc = FleetService(db=session)

        assert svc.disband_fleet(uuid4()) is False
