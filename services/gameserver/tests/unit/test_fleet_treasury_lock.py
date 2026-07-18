"""WO-FLEET-TREASURY-LOCK sub-part (a) — closes the treasury lost-update in
``FleetService._apply_battle_loot``.

The bug: ``_apply_battle_loot`` used to read/write ``attacker.team.
treasury_credits`` / ``defender.team.treasury_credits`` through the Fleet ->
Team relationship completely UNLOCKED. The FleetBattle + both Fleet FOR-
UPDATE locks the caller (``simulate_battle_round``) holds do NOT cover the
FK-related Team rows. Meanwhile ``team_service.py``'s ``deposit_to_treasury``
/ ``withdraw_from_treasury`` / ``transfer_to_player`` mutate the SAME
``treasury_credits`` field UNDER ``with_for_update()`` (via
``setattr(team, f"treasury_{resource_type}", ...)``). A LOCKED team-service
call racing this UNLOCKED battle-end loot on the same Team row is a classic
lost-update.

The fix: a new ``_lock_teams_ascending`` helper (mirroring
``_lock_fleets_ascending`` / ``_lock_players_ascending``'s established
ascending-id N-row pattern) locks BOTH involved Team rows before any
``treasury_credits`` read in ``_apply_battle_loot``. Same-team battles
(currently blocked at ``initiate_battle``, kept defensive here since
``_end_battle`` is also reachable from the admin force-winner path) dedupe
to ONE lock acquisition via the set-collapse, and the two team references
resolve to the identical object so the credit-then-debit nets to a wash.

Same DB-free, hand-rolled call-order-spy convention as
test_fleet_kill_lock_order.py (which itself proves ``_lock_players_
ascending``'s ordering the identical way): a ``_FakeQuery``/``_FakeSession``
records ``with_for_update()`` acquisitions in an ordered ``events`` list, and
an ``_EventTeam`` stand-in additionally logs every ``treasury_credits``
read/write into that SAME list, so lock-vs-treasury-access ordering is
directly observable — not just lock-vs-lock order. True concurrent DB-level
blocking (a second transaction's locked write genuinely waiting on this row
lock) needs real Postgres — the orchestrator's leg, per this suite's
established sibling precedent.

REVISION (both gates: mack + cipher) — the ``TestRoundPathLocksTeamBefore
Player`` suite at the bottom of this file adds the regression pin for the
follow-up fix: the un-hoisted shape (Team locked only inside
``_apply_battle_loot``, i.e. at the very END of ``_end_battle``) was a
SHIP-BLOCKER, not a fast-follow — the 70%-loss battle-end threshold fires
the SAME round a kill tallies, so the mid-round Player lock from
``_distribute_fleet_kill_rewards`` ALWAYS preceded the battle-end Team lock
on that closing round: Player-then-Team, the reverse of ``team_service.py``'s
own Team-then-Player order and this codebase's documented resource-before-
player deadlock contract (``trading.py:513``, ``planet_grid.py:245``,
``auth.py:549``). The fix hoists a ``_lock_teams_ascending`` call to the top
of ``simulate_battle_round`` (right after the Fleet lock, before the fire
loop can reach the kill-reward Player lock), restoring FleetBattle -> Fleet
-> Team -> Player as the round's full lock order. That suite exercises a
REAL ``simulate_battle_round`` call (real ORM Fleet/FleetMember/Ship/
FleetBattle/Team instances, same idiom as test_fleet_round_integrity.py's
``TestSimulateBattleRoundSingleCommit``, extended with per-model lock-order
event logging) through a round that both kills a ship AND ends the battle in
the same round — the exact scenario that made this a live, player-
triggerable deadlock rather than a theoretical one.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

from src.models.fleet import (
    BattlePhase, Fleet, FleetBattle, FleetMember, FleetRole, FleetStatus,
)
from src.models.ship import Ship, ShipType
from src.models.team import Team
from src.services import fleet_service as fs
from src.services.fleet_service import FleetService

# --------------------------------------------------------------------------- #
# Fake DB — records Team lock acquisitions AND treasury_credits read/write
# access, interleaved in one ordered events list.
# --------------------------------------------------------------------------- #

class _EventTeam:
    """Team stand-in whose ``treasury_credits`` accesses are logged into a
    shared events list, so read/write ordering relative to the Team lock
    acquisition is directly observable."""

    def __init__(self, *, team_id, treasury_credits, events):
        object.__setattr__(self, "id", team_id)
        object.__setattr__(self, "_treasury_credits", treasury_credits)
        object.__setattr__(self, "_events", events)

    @property
    def treasury_credits(self):
        self._events.append(("read", self.id))
        return self._treasury_credits

    @treasury_credits.setter
    def treasury_credits(self, value):
        self._events.append(("write", self.id, value))
        object.__setattr__(self, "_treasury_credits", value)


class _FakeQuery:
    """Routes a ``Team.id == <literal>`` filter to the matching seeded row
    and appends a ``("lock", id)`` event, in call order, to the SAME ordered
    ``events`` list the owning ``_FakeSession`` uses (mirrors
    test_fleet_kill_lock_order.py's ``_FakeQuery`` exactly, for Team instead
    of Player)."""

    def __init__(self, teams, events):
        self._teams = teams
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
        return self._teams.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Team store; records lock acquisitions, flush calls, and
    (via ``_EventTeam``) treasury_credits reads/writes -- all interleaved in
    one ordered ``events`` list shared with the seeded ``_EventTeam``s."""

    def __init__(self, *teams, events=None):
        self.events: list = events if events is not None else []
        self._teams = {t.id: t for t in teams}
        self.added: list = []

    @property
    def team_lock_log(self):
        return [e[1] for e in self.events if e[0] == "lock"]

    def query(self, model):
        return _FakeQuery(self._teams, self.events)

    def flush(self):
        self.events.append(("flush",))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def make_session_with_teams(*treasuries, team_ids=None):
    """Build a _FakeSession plus one _EventTeam per given treasury balance,
    all sharing ONE events list, so lock-vs-treasury-access ordering is
    observable straight off ``session.events``. Returns (session, [teams])
    in the SAME order as the given treasury balances."""
    events: list = []
    ids = list(team_ids) if team_ids is not None else [uuid4() for _ in treasuries]
    teams = [
        _EventTeam(team_id=tid, treasury_credits=tc, events=events)
        for tid, tc in zip(ids, treasuries, strict=True)
    ]
    session = _FakeSession(*teams, events=events)
    return session, teams


def make_fleet(*, team_id):
    """Fleet stand-in: _apply_battle_loot only ever reads the raw
    ``team_id`` FK off its Fleet params (never the ``.team`` relationship),
    so a bare SimpleNamespace is sufficient."""
    return SimpleNamespace(id=uuid4(), team_id=team_id)


def make_battle(*, winner):
    return SimpleNamespace(winner=winner, credits_looted=0, id=uuid4())


# --------------------------------------------------------------------------- #
# 1. _lock_teams_ascending in isolation -- mirrors TestLockPlayersAscending-
#    Helper in test_fleet_kill_lock_order.py.
# --------------------------------------------------------------------------- #

class TestLockTeamsAscendingHelper:
    def test_locks_in_ascending_order_regardless_of_input_order(self):
        a, b, c = sorted(uuid4() for _ in range(3))
        session, _teams = make_session_with_teams(0, 0, 0, team_ids=[a, b, c])
        service = FleetService(session)

        locked = service._lock_teams_ascending({c, a, b})

        assert session.team_lock_log == [a, b, c]
        assert set(locked.keys()) == {a, b, c}

    def test_missing_team_id_is_skipped_not_kept_as_none(self):
        tid = uuid4()
        missing_id = uuid4()
        session, _teams = make_session_with_teams(0, team_ids=[tid])
        service = FleetService(session)

        locked = service._lock_teams_ascending({tid, missing_id})

        assert tid in locked
        assert missing_id not in locked


# --------------------------------------------------------------------------- #
# 2. _apply_battle_loot -- both Team rows lock BEFORE any treasury_credits
#    read, regardless of which side (attacker/defender) sorts lower.
# --------------------------------------------------------------------------- #

class TestApplyBattleLootLocksBeforeTreasuryRead:
    def _assert_each_team_locked_before_first_access(self, session, team_ids):
        for tid in team_ids:
            lock_idx = session.events.index(("lock", tid))
            access_idxs = [
                i for i, e in enumerate(session.events)
                if e[0] in ("read", "write") and e[1] == tid
            ]
            assert access_idxs, f"team {tid} was never accessed"
            assert lock_idx < min(access_idxs)

    def test_attacker_win_both_teams_locked_before_the_first_treasury_read(self):
        session, (attacker_team, defender_team) = make_session_with_teams(0, 1000)
        service = FleetService(session)
        attacker = make_fleet(team_id=attacker_team.id)
        defender = make_fleet(team_id=defender_team.id)
        battle = make_battle(winner="attacker")

        service._apply_battle_loot(battle, attacker, defender)

        assert set(session.team_lock_log) == {attacker_team.id, defender_team.id}
        assert len(session.team_lock_log) == 2  # each locked exactly once
        self._assert_each_team_locked_before_first_access(
            session, [attacker_team.id, defender_team.id]
        )

        loot = 1000 // 10
        assert defender_team.treasury_credits == 1000 - loot
        assert attacker_team.treasury_credits == loot
        assert battle.credits_looted == loot

    def test_defender_win_both_teams_locked_before_the_first_treasury_read(self):
        session, (attacker_team, defender_team) = make_session_with_teams(2000, 0)
        service = FleetService(session)
        attacker = make_fleet(team_id=attacker_team.id)
        defender = make_fleet(team_id=defender_team.id)
        battle = make_battle(winner="defender")

        service._apply_battle_loot(battle, attacker, defender)

        assert set(session.team_lock_log) == {attacker_team.id, defender_team.id}
        assert len(session.team_lock_log) == 2
        self._assert_each_team_locked_before_first_access(
            session, [attacker_team.id, defender_team.id]
        )

        loot = 2000 // 10
        assert attacker_team.treasury_credits == 2000 - loot
        assert defender_team.treasury_credits == loot
        assert battle.credits_looted == loot

    def test_lock_order_is_ascending_id_not_attacker_first(self):
        """Role-reversal proof (mirrors TestRoleReversalConverges in
        test_fleet_kill_lock_order.py): the lock order must converge on
        ascending team id regardless of whether the attacker's or the
        defender's team happens to sort lower -- otherwise two racing
        battle-end settlements sharing a team pair with attacker/defender
        roles reversed could lock in opposite orders (AB-BA)."""
        low_id, high_id = sorted([uuid4(), uuid4()])

        # Case 1: attacker's team sorts LOW.
        session1, _ = make_session_with_teams(0, 1000, team_ids=[low_id, high_id])
        FleetService(session1)._apply_battle_loot(
            make_battle(winner="attacker"),
            make_fleet(team_id=low_id),
            make_fleet(team_id=high_id),
        )
        assert session1.team_lock_log == [low_id, high_id]

        # Case 2: attacker's team sorts HIGH (roles reversed vs case 1) --
        # must converge on the SAME ascending order.
        session2, _ = make_session_with_teams(0, 1000, team_ids=[high_id, low_id])
        FleetService(session2)._apply_battle_loot(
            make_battle(winner="attacker"),
            make_fleet(team_id=high_id),
            make_fleet(team_id=low_id),
        )
        assert session2.team_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# 3. Same-team dedupe -- both fleets resolve to ONE Team row.
# --------------------------------------------------------------------------- #

class TestSameTeamDedupe:
    def test_same_team_locks_only_once(self):
        tid = uuid4()
        session, (team,) = make_session_with_teams(1000, team_ids=[tid])
        service = FleetService(session)

        service._apply_battle_loot(
            make_battle(winner="attacker"),
            make_fleet(team_id=tid),
            make_fleet(team_id=tid),
        )

        assert session.team_lock_log == [tid]  # exactly ONE acquisition, not two

    def test_same_team_credit_then_debit_nets_to_a_wash(self):
        """Both fleets on one team (friendly-fire settlement, defensive-only
        path -- initiate_battle blocks this at creation): the credit and
        debit land on the SAME object, so the treasury nets back to its
        pre-battle value even though credits_looted records the gross
        amount moved."""
        tid = uuid4()
        session, (team,) = make_session_with_teams(1000, team_ids=[tid])
        service = FleetService(session)
        battle = make_battle(winner="attacker")

        service._apply_battle_loot(
            battle,
            make_fleet(team_id=tid),
            make_fleet(team_id=tid),
        )

        assert team.treasury_credits == 1000  # wash: unchanged net
        assert battle.credits_looted == 100  # 10% of the pre-battle treasury


# --------------------------------------------------------------------------- #
# 4. Naive-safe design pin -- no upstream flush() ahead of the lock (the
#    caller-trace conclusion: no path mutates either Team row on this
#    session before _apply_battle_loot's lock, so no flush is needed).
# --------------------------------------------------------------------------- #

class TestNoUpstreamFlushNeeded:
    def test_apply_battle_loot_never_calls_flush(self):
        """Regression/design pin: if a future change introduces an unlocked
        Team mutation upstream of this method on the SAME session, the
        naive-safe trace in _apply_battle_loot's docstring would need a
        flush() added ahead of the lock -- this test documents that today
        no flush is emitted, so a reviewer changing this must consciously
        update both the code and this test together."""
        session, (attacker_team, defender_team) = make_session_with_teams(0, 500)
        service = FleetService(session)

        service._apply_battle_loot(
            make_battle(winner="attacker"),
            make_fleet(team_id=attacker_team.id),
            make_fleet(team_id=defender_team.id),
        )

        assert not any(e[0] == "flush" for e in session.events)


# --------------------------------------------------------------------------- #
# 5. Structural pin -- the lock call precedes any treasury_credits usage in
#    source, and the helper routes through populate_existing+with_for_update.
# --------------------------------------------------------------------------- #

class TestStructuralPin:
    def test_apply_battle_loot_locks_teams_before_first_treasury_credits_reference(self):
        """Searches for the ATTRIBUTE-ACCESS form (``.treasury_credits``,
        dot-prefixed) rather than the bare word, so the docstring's own
        prose mentions (backtick-wrapped, never dot-prefixed) don't produce
        a false "read" position ahead of the real code."""
        source = inspect.getsource(FleetService._apply_battle_loot)
        lock_idx = source.index("self._lock_teams_ascending(")
        first_treasury_idx = source.index(".treasury_credits")
        assert lock_idx < first_treasury_idx

    def test_lock_teams_ascending_uses_populate_existing_and_with_for_update(self):
        source = inspect.getsource(FleetService._lock_teams_ascending)
        assert ".populate_existing().with_for_update()" in source


# --------------------------------------------------------------------------- #
# 6. No-op / degenerate cases -- neither fleet resolves to a team, or a draw.
# --------------------------------------------------------------------------- #

class TestDegenerateNoTeamCases:
    def test_no_fleets_is_a_pure_noop(self):
        session, _teams = make_session_with_teams()
        service = FleetService(session)
        battle = make_battle(winner="attacker")

        service._apply_battle_loot(battle, None, None)

        assert battle.credits_looted == 0
        assert session.team_lock_log == []

    def test_draw_never_moves_treasury(self):
        session, (attacker_team, defender_team) = make_session_with_teams(100, 100)
        service = FleetService(session)
        battle = make_battle(winner="draw")

        service._apply_battle_loot(
            battle,
            make_fleet(team_id=attacker_team.id),
            make_fleet(team_id=defender_team.id),
        )

        # A draw never had a decisive winner in the original code either --
        # neither branch fires, so neither Team row is even locked (matches
        # the pre-fix zero-Team-touch behavior exactly, just now provably
        # true under the new lock gate too).
        assert session.team_lock_log == []
        assert battle.credits_looted == 0
        assert attacker_team.treasury_credits == 100
        assert defender_team.treasury_credits == 100


# --------------------------------------------------------------------------- #
# 7. REVISION regression pin -- the round path locks Team BEFORE the first
#    Player lock, even on a round that both kills a ship AND ends the battle
#    in the same round (the exact scenario that made the un-hoisted shape a
#    live, player-triggerable AB-BA deadlock rather than a theoretical one).
#
#    Exercises a REAL simulate_battle_round call -- real ORM Fleet/
#    FleetMember/Ship/FleetBattle/Team instances, same fixture idiom as
#    test_fleet_round_integrity.py's TestSimulateBattleRoundSingleCommit,
#    extended with per-model lock-order event logging (mirrors
#    test_fleet_kill_lock_order.py's _FakeQuery, generalized to any model).
#    BountyService.collect_bounty_share is mocked to its cheapest path
#    (had_bounty=True, paid=0) so _distribute_fleet_kill_rewards's OWN lock-
#    acquisition code runs for real without needing to fake JSONB bounty-pot
#    internals -- the identical mocking boundary test_fleet_kill_lock_order.py
#    already established for proving Player-lock ordering.
# --------------------------------------------------------------------------- #

def _flatten_conditions(conditions):
    flat = []
    for c in conditions:
        if hasattr(c, "clauses"):  # and_(...) -> BooleanClauseList
            flat.extend(c.clauses)
        else:
            flat.append(c)
    return flat


def _condition_matches_row(row, condition):
    return getattr(row, condition.left.key) == condition.right.value


class _OrderedFakeQuery:
    """Generalization of test_fleet_kill_lock_order.py's ``_FakeQuery`` to
    ANY model: interprets the SUT's real SQLAlchemy filter() conditions
    against a live per-model pool (needed here because simulate_battle_round's
    full path queries FleetBattle, Fleet, Team, Player, AND FleetMember --
    not just one model), while STILL logging every ``with_for_update()``
    acquisition as ``("lock", <ModelName>, <id>)`` into the owning session's
    shared ``events`` list, keyed off a simple ``Model.id == <literal>``
    match (every lock call site this suite exercises filters that way)."""

    def __init__(self, pool, session, model_name):
        self._pool = pool
        self._session = session
        self._model_name = model_name
        self._conditions = []
        self._match_id = None

    def filter(self, *conditions):
        flat = _flatten_conditions(conditions)
        self._conditions = self._conditions + flat
        for c in flat:
            rhs = getattr(c, "right", None)
            val = getattr(rhs, "value", None)
            if val is not None:
                self._match_id = val
        return self

    def with_for_update(self, *a, **k):
        if self._match_id is not None:
            self._session.events.append(("lock", self._model_name, self._match_id))
        return self

    def populate_existing(self, *a, **k):
        return self

    def _matching(self):
        return [r for r in self._pool if all(_condition_matches_row(r, c) for c in self._conditions)]

    def first(self):
        matches = self._matching()
        return matches[0] if matches else None

    def all(self):
        return list(self._matching())

    def delete(self) -> int:
        matches = self._matching()
        for row in matches:
            if row in self._pool:
                self._pool.remove(row)
            if isinstance(row, FleetMember):
                row.fleet = None
        return len(matches)


class _OrderedFakeSession:
    """Maps a model class -> its live pool (mirrors test_fleet_round_
    integrity.py's ``_FakeSession``), plus ONE shared ordered ``events`` list
    recording every ``("lock", ModelName, id)`` acquisition AND every
    ``("flush",)`` call, so lock-vs-lock AND lock-vs-flush ordering across
    DIFFERENT models is directly observable in call order."""

    def __init__(self, pools):
        self._pools = pools
        self.events: list = []
        self.added: list = []
        self.commit_count = 0
        self.flush_count = 0

    @property
    def lock_log(self):
        """[(ModelName, id), ...] in acquisition order, across ALL models."""
        return [(e[1], e[2]) for e in self.events if e[0] == "lock"]

    def query(self, model):
        return _OrderedFakeQuery(self._pools.get(model, []), self, model.__name__)

    def add(self, obj):
        self.added.append(obj)
        self._pools.setdefault(type(obj), []).append(obj)

    def delete(self, obj):
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
        self.events.append(("flush",))


def orm_make_team(*, treasury_credits=0):
    return Team(id=uuid4(), name=f"team-{uuid4().hex[:8]}", treasury_credits=treasury_credits)


def orm_make_fleet(*, team=None, status=FleetStatus.IN_BATTLE.value):
    fleet = Fleet(
        id=uuid4(),
        team_id=team.id if team else uuid4(),
        commander_id=None,
        name="Test Fleet",
        status=status,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
    )
    fleet.team = team
    return fleet


def orm_make_ship(*, hull=100, max_hull=100, shields=0, attack_rating=10, owner_id=None):
    return Ship(
        id=uuid4(),
        name="Ship",
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


def orm_make_member(*, fleet, ship, role=FleetRole.ATTACKER):
    member = FleetMember(
        id=uuid4(),
        fleet_id=fleet.id,
        ship_id=ship.id,
        player_id=ship.owner_id,
        role=role.value if hasattr(role, "value") else role,
    )
    member.fleet = fleet  # back_populates -> fleet.members
    member.ship = ship
    return member


def orm_make_battle(*, attacker_fleet, defender_fleet, phase=BattlePhase.ENGAGEMENT.value):
    battle = FleetBattle(
        id=uuid4(),
        attacker_fleet_id=attacker_fleet.id,
        defender_fleet_id=defender_fleet.id,
        phase=phase,
        battle_log=[],
        attacker_damage_dealt=0,
        defender_damage_dealt=0,
        total_damage_dealt=0,
    )
    battle.attacker_fleet = attacker_fleet
    battle.defender_fleet = defender_fleet
    return battle


class TestRoundPathLocksTeamBeforePlayer:
    def test_team_lock_precedes_first_player_lock_on_a_kill_and_end_round(self, monkeypatch):
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

        # Cheapest BountyService path (mirrors test_fleet_kill_lock_order.py):
        # had_bounty=True, paid=0 skips both the +100 rep award and the whole
        # innocent-penalty/grey-flag branch, while letting
        # _distribute_fleet_kill_rewards's OWN flush + _lock_players_ascending
        # call run for real -- that call is exactly what this test needs to
        # observe the ordering of.
        from src.services import bounty_service as bounty_service_module

        def _fake_collect_bounty_share(self, hunter_id, target_id, num_participants, claim_player_pot):
            return {"success": True, "had_bounty": True, "paid": 0, "system_paid": 0, "player_paid": 0}

        monkeypatch.setattr(
            bounty_service_module.BountyService, "collect_bounty_share", _fake_collect_bounty_share
        )

        attacker_team = orm_make_team(treasury_credits=0)
        defender_team = orm_make_team(treasury_credits=500)
        attacker_fleet = orm_make_fleet(team=attacker_team)
        defender_fleet = orm_make_fleet(team=defender_team)
        a_ship = orm_make_ship(hull=1000, max_hull=1000, attack_rating=1000)  # overwhelming
        d_ship = orm_make_ship(hull=1, max_hull=100)  # one-shot fragile
        a_member = orm_make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = orm_make_member(fleet=defender_fleet, ship=d_ship)

        battle = orm_make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet)
        session = _OrderedFakeSession({
            FleetMember: [a_member, d_member],
            FleetBattle: [battle],
            Fleet: [attacker_fleet, defender_fleet],
            Team: [attacker_team, defender_team],
        })
        svc = FleetService(db=session)

        monkeypatch.setattr(fs.random, "random", lambda: 0.1)   # every hit-chance roll succeeds
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        result = svc.simulate_battle_round(battle.id)

        # Sanity: the round both killed a ship AND ended the battle THIS
        # round -- the exact scenario the un-hoisted shape mishandled.
        assert result["battle_ongoing"] is False
        assert battle.ended_at is not None
        assert d_member not in session._pools[FleetMember]  # the casualty removal did happen

        # THE regression pin: a Team lock exists in the log, a Player lock
        # exists in the log, and the FIRST Team lock precedes the FIRST
        # Player lock -- Fleet -> Team -> Player, not Player -> Team.
        team_lock_idxs = [i for i, (model, _id) in enumerate(session.lock_log) if model == "Team"]
        player_lock_idxs = [i for i, (model, _id) in enumerate(session.lock_log) if model == "Player"]
        assert team_lock_idxs, "no Team lock was acquired at all"
        assert player_lock_idxs, "no Player lock was acquired at all -- the kill-reward path didn't run"
        assert min(team_lock_idxs) < min(player_lock_idxs)

        # Full ordered lock set sanity: FleetBattle, then both Fleets
        # (ascending), then both Teams (ascending, the hoist), THEN Player(s)
        # from the kill-reward path -- never the reverse.
        models_in_order = [model for model, _id in session.lock_log]
        assert models_in_order[0] == "FleetBattle"
        first_team_pos = models_in_order.index("Team")
        first_player_pos = models_in_order.index("Player")
        first_fleet_pos = models_in_order.index("Fleet")
        assert first_fleet_pos < first_team_pos < first_player_pos

    def test_apply_battle_loots_own_relock_targets_already_held_rows_only(self, monkeypatch):
        """Belt-and-suspenders on _apply_battle_loot's OWN _lock_teams_
        ascending call (left completely unchanged by the hoist, still fires
        unconditionally on the decisive-winner path -- see its docstring).
        That call runs AFTER the fire loop's Player lock, so it DOES appear
        later in the event log than the Player lock -- that is expected and
        SAFE, not a regression: a transaction re-acquiring FOR UPDATE on a
        row it already holds is a documented Postgres no-op (a transaction
        never blocks on its own held lock). What actually matters for
        deadlock-safety is narrower and is pinned here precisely: (1) the
        FIRST Team lock (the hoist) still precedes the first Player lock,
        and (2) every Team id locked AFTER that first Player lock was
        ALREADY covered by the hoist's first batch -- i.e. _apply_battle_
        loot's re-lock never acquires a team id for the FIRST time post-
        Player, which is the one shape that would actually still be
        dangerous."""
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

        from src.services import bounty_service as bounty_service_module

        def _fake_collect_bounty_share(self, hunter_id, target_id, num_participants, claim_player_pot):
            return {"success": True, "had_bounty": True, "paid": 0, "system_paid": 0, "player_paid": 0}

        monkeypatch.setattr(
            bounty_service_module.BountyService, "collect_bounty_share", _fake_collect_bounty_share
        )

        attacker_team = orm_make_team(treasury_credits=0)
        defender_team = orm_make_team(treasury_credits=500)
        attacker_fleet = orm_make_fleet(team=attacker_team)
        defender_fleet = orm_make_fleet(team=defender_team)
        a_ship = orm_make_ship(hull=1000, max_hull=1000, attack_rating=1000)
        d_ship = orm_make_ship(hull=1, max_hull=100)
        a_member = orm_make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = orm_make_member(fleet=defender_fleet, ship=d_ship)

        battle = orm_make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet)
        session = _OrderedFakeSession({
            FleetMember: [a_member, d_member],
            FleetBattle: [battle],
            Fleet: [attacker_fleet, defender_fleet],
            Team: [attacker_team, defender_team],
        })
        svc = FleetService(db=session)

        monkeypatch.setattr(fs.random, "random", lambda: 0.1)
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        svc.simulate_battle_round(battle.id)

        # The loot transfer genuinely happened (proves _apply_battle_loot's
        # own re-lock didn't somehow short-circuit or skip the mutation).
        assert battle.winner == "attacker"
        loot = 500 // 10
        assert defender_team.treasury_credits == 500 - loot
        assert attacker_team.treasury_credits == loot

        team_lock_idxs = [i for i, (model, _id) in enumerate(session.lock_log) if model == "Team"]
        player_lock_idxs = [i for i, (model, _id) in enumerate(session.lock_log) if model == "Player"]
        assert team_lock_idxs and player_lock_idxs
        first_player_idx = min(player_lock_idxs)

        # (1) The FIRST Team lock (the hoist) precedes the first Player lock.
        assert min(team_lock_idxs) < first_player_idx

        # (2) Any Team lock event AFTER the first Player lock (i.e.
        # _apply_battle_loot's own re-lock call) targets ONLY team ids
        # already covered by the hoist's pre-Player batch -- confirming it's
        # a genuine same-transaction re-lock, never a first-time acquisition
        # of a new team id slipping in post-Player.
        pre_player_team_ids = {
            tid for i, (model, tid) in enumerate(session.lock_log)
            if model == "Team" and i < first_player_idx
        }
        post_player_team_ids = {
            tid for i, (model, tid) in enumerate(session.lock_log)
            if model == "Team" and i > first_player_idx
        }
        assert post_player_team_ids <= pre_player_team_ids, (
            "a Team lock acquired AFTER the first Player lock targeted a "
            "team id not already held from the hoist -- that would be a "
            "genuine first-time acquisition racing the deadlock contract, "
            "not a safe same-transaction re-lock"
        )
