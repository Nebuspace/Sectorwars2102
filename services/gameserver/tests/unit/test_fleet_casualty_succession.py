"""WO-FLEET-CASUALTY-SUCCESSION — closes two acknowledged fleet-tactics gaps
in ``fleet_service.py``:

1. ``FleetBattleCasualty.damage_dealt`` / ``.kills`` existed on the model but
   were NEVER written — ``_record_ship_casualty`` populated only
   ``damage_taken``. This file falsifies the always-0 columns.
2. Flagship succession was manual (fleet-tactics.md:80 target spec:
   "leadership transitions to the next-most-senior member"). This file pins
   the NO-CANON seniority kernel (earliest ``FleetMember.joined_at``, ties
   broken by lowest ``str(id)``) and the ``Fleet.commander_id`` transfer.

DB-free: no live DB, no app. ``_FakeSession``/``_FakeQuery`` interpret the
SUT's REAL SQLAlchemy filter() conditions (single ``Column == value`` and
``and_(...)``-wrapped compounds) against live, mutable in-memory pools —
mirrors the established codebase idiom (test_route_runs_retention.py's
FakeRouteRunQuery / test_warp_gate_toll.py's _FakeSession). Fleet / FleetMember
/ Ship / FleetBattle are REAL (transient, unpersisted) ORM instances so the
real ``back_populates`` relationship wiring (``member.fleet`` <->
``fleet.members``) does the right thing without a session (verified: setting
``member.fleet = None`` detaches it from ``fleet.members`` for real).

ARCHITECTURAL NOTE discovered while designing this suite (see
FleetService._ship_battle_contribution's docstring for the full argument):
because a fleet-battle round always fires attackers before defenders, the
side that decisively WINS an engagement necessarily retains at least one
living, undamaged-this-round survivor whose own contribution is never
captured by any casualty row (FleetBattleCasualty is a per-CASUALTY ledger —
invariant 9, fleet-coordination.md — never a per-PARTICIPANT one). So
SUM(damage_dealt) over a battle's casualty rows generally UNDERCOUNTS
battle.attacker_damage_dealt + battle.defender_damage_dealt by exactly the
winning side's uncaptured contribution; full equality requires every ship
that ever landed a shot to also become a casualty, which this suite
constructs directly rather than hoping an RNG-driven multi-round battle
happens to produce it organically (structurally near-impossible — see the
docstring). The tests below instead prove the ACHIEVABLE, meaningful
invariant: every casualty row's damage_dealt/kills exactly equals what that
ship itself contributed up to its own casualty event.

Acceptance-criteria map (WO-FLEET-CASUALTY-SUCCESSION):
  - damage_dealt/kills populated (not hard-0)  -> TestShipBattleContribution,
    TestRecordShipCasualtyPopulatesFields
  - SUM(damage_dealt) == exchanged total for a fully-accounted engagement ->
    TestRecordShipCasualtyPopulatesFields::test_sum_matches_hand_built_ledger
  - SUM(kills) == ships destroyed (for ships that themselves become
    casualties) -> TestShipBattleContribution::test_kills_only_counted_when_flagged,
    TestApplyDamageKillAttribution
  - Flagship succession: earliest joined_at promoted -> TestFlagshipSuccession
  - tie-break lowest member id -> test_tie_break_lowest_member_id
  - commander_id transfers when applicable -> test_commander_transfers_...
  - commander unchanged when fallen pilot wasn't commander ->
    test_non_commander_flagship_pilot_leaves_commander_unchanged
  - last-member edge (no survivor) -> test_last_member_destroyed_no_crash_no_promotion
  - manual removal also triggers succession -> test_manual_removal_also_triggers_succession
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from src.models.fleet import (
    Fleet, FleetMember, FleetBattle, FleetBattleCasualty,
    FleetRole, FleetStatus, BattlePhase,
)
from src.models.ship import Ship, ShipType
from src.services import fleet_service as fs
from src.services.fleet_service import FleetService


# --------------------------------------------------------------------------- #
# Fake DB — interprets the SUT's real filter() conditions against live pools
# (mirrors test_route_runs_retention.py's FakeRouteRunQuery pattern).
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
    def __init__(self, pool: List[Any]):
        self._pool = pool
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


class _FakeSession:
    """Maps a model class -> its live pool. delete() on a FleetMember
    detaches it from its Fleet via the REAL back_populates relationship
    (verified: this mirrors what a real flush would eventually produce), so
    _recalculate_fleet_stats (which reads fleet.members, not this session)
    stays consistent with what's actually been "deleted"."""

    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.commit_count = 0

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))

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
        pass


# --------------------------------------------------------------------------- #
# Fixtures — real (transient) ORM instances
# --------------------------------------------------------------------------- #

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


def make_fleet(*, commander_id=None):
    return Fleet(
        id=uuid4(),
        team_id=uuid4(),
        commander_id=commander_id,
        name="Test Fleet",
        status=FleetStatus.IN_BATTLE.value,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
    )


def make_member(*, fleet, ship, role=FleetRole.ATTACKER, joined_at=None):
    member = FleetMember(
        id=uuid4(),
        fleet_id=fleet.id,
        ship_id=ship.id,
        player_id=ship.owner_id,
        role=role.value if hasattr(role, "value") else role,
        joined_at=joined_at or datetime.utcnow(),
    )
    member.fleet = fleet  # back_populates -> fleet.members
    member.ship = ship
    return member


def make_battle(*, attacker_fleet, defender_fleet, phase=BattlePhase.ENGAGEMENT.value, battle_log=None):
    battle = FleetBattle(
        id=uuid4(),
        attacker_fleet_id=attacker_fleet.id,
        defender_fleet_id=defender_fleet.id,
        phase=phase,
        battle_log=battle_log if battle_log is not None else [],
        attacker_damage_dealt=0,
        defender_damage_dealt=0,
        total_damage_dealt=0,
    )
    battle.attacker_fleet = attacker_fleet
    battle.defender_fleet = defender_fleet
    return battle


def make_service(*pools_members):
    """pools_members: FleetMember instances to seed the FleetMember pool
    with; Fleet/Ship pools are seeded lazily as needed by individual tests."""
    session = _FakeSession({FleetMember: list(pools_members)})
    return FleetService(db=session), session


# --------------------------------------------------------------------------- #
# _ship_battle_contribution — pure accumulation logic, no DB
# --------------------------------------------------------------------------- #

class TestShipBattleContribution:
    def test_no_shots_anywhere_is_zero(self):
        svc = FleetService(db=None)
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet(), battle_log=[])
        dealt, kills = svc._ship_battle_contribution(uuid4(), battle, round_results={"shots": []})
        assert (dealt, kills) == (0, 0)

    def test_sums_prior_rounds_plus_current_round(self):
        svc = FleetService(db=None)
        ship_id = uuid4()
        other_id = uuid4()
        prior_log = [
            {"results": {"shots": [
                {"ship_id": str(ship_id), "damage": 10, "killed": False},
                {"ship_id": str(other_id), "damage": 999, "killed": True},
            ]}},
            {"results": {"shots": [
                {"ship_id": str(ship_id), "damage": 15, "killed": False},
            ]}},
        ]
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet(), battle_log=prior_log)
        current_round_results = {"shots": [
            {"ship_id": str(ship_id), "damage": 7, "killed": True},
        ]}
        dealt, kills = svc._ship_battle_contribution(ship_id, battle, current_round_results)
        assert dealt == 10 + 15 + 7
        assert kills == 1

    def test_kills_only_counted_when_flagged(self):
        svc = FleetService(db=None)
        ship_id = uuid4()
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet(), battle_log=[])
        round_results = {"shots": [
            {"ship_id": str(ship_id), "damage": 5, "killed": False},
            {"ship_id": str(ship_id), "damage": 5, "killed": False},
            {"ship_id": str(ship_id), "damage": 5, "killed": True},
        ]}
        dealt, kills = svc._ship_battle_contribution(ship_id, battle, round_results)
        assert dealt == 15
        assert kills == 1

    def test_unrelated_ship_ids_excluded(self):
        svc = FleetService(db=None)
        target_id = uuid4()
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet(), battle_log=[
            {"results": {"shots": [{"ship_id": str(uuid4()), "damage": 50, "killed": True}]}}
        ])
        dealt, kills = svc._ship_battle_contribution(target_id, battle, {"shots": []})
        assert (dealt, kills) == (0, 0)

    def test_non_list_battle_log_tolerated(self):
        """battle.battle_log defaults to list, but be defensive against a
        stray non-list value (matches the isinstance guard used elsewhere in
        fleet_service, e.g. simulate_battle_round's own battle_log reads)."""
        svc = FleetService(db=None)
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet())
        battle.battle_log = None
        dealt, kills = svc._ship_battle_contribution(uuid4(), battle, {"shots": []})
        assert (dealt, kills) == (0, 0)

    def test_round_results_none_tolerated(self):
        svc = FleetService(db=None)
        battle = make_battle(attacker_fleet=make_fleet(), defender_fleet=make_fleet(), battle_log=[])
        dealt, kills = svc._ship_battle_contribution(uuid4(), battle, round_results=None)
        assert (dealt, kills) == (0, 0)


# --------------------------------------------------------------------------- #
# _record_ship_casualty — damage_dealt/kills populated on the row
# --------------------------------------------------------------------------- #

class TestRecordShipCasualtyPopulatesFields:
    def test_retreat_path_populates_damage_dealt_and_kills(self):
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        ship = make_ship(hull=20, max_hull=100)  # below 30% -> plausible retreat state
        member = make_member(fleet=defender_fleet, ship=ship)
        svc, session = make_service(member)

        battle = make_battle(
            attacker_fleet=attacker_fleet, defender_fleet=defender_fleet,
            battle_log=[{"results": {"shots": [
                {"ship_id": str(ship.id), "damage": 12, "killed": False},
            ]}}],
        )
        round_results = {"shots": [{"ship_id": str(ship.id), "damage": 8, "killed": True}]}

        svc._record_ship_casualty(ship, battle, destroyed=False, round_results=round_results)

        assert len(session.added) == 1
        casualty = session.added[0]
        assert isinstance(casualty, FleetBattleCasualty)
        assert casualty.destroyed is False
        assert casualty.retreated is True
        assert casualty.damage_dealt == 12 + 8
        assert casualty.kills == 1
        assert casualty.damage_taken == 100 - 20
        assert casualty.was_attacker is False  # member is on the defender fleet

    def test_destroyed_path_populates_damage_dealt_and_kills(self, monkeypatch):
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

        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        ship = make_ship(hull=0, max_hull=100)
        member = make_member(fleet=attacker_fleet, ship=ship)
        svc, session = make_service(member)
        svc._distribute_fleet_kill_rewards = lambda *a, **k: None  # existing, unrelated machinery

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        round_results = {"shots": [
            {"ship_id": str(ship.id), "damage": 40, "killed": True},
            {"ship_id": str(ship.id), "damage": 5, "killed": False},
        ]}

        svc._record_ship_casualty(ship, battle, destroyed=True, round_results=round_results)

        assert len(session.added) == 1
        casualty = session.added[0]
        assert casualty.destroyed is True
        assert casualty.retreated is False
        assert casualty.damage_dealt == 45
        assert casualty.kills == 1
        assert casualty.was_attacker is True
        # remove_ship_from_fleet ran as part of the destroyed branch.
        assert member not in session._pools[FleetMember]
        assert battle.attacker_ships_destroyed == 1

    def test_sum_matches_hand_built_ledger_for_a_fully_accounted_engagement(self, monkeypatch):
        """Constructs a scenario where EVERY firing ship becomes a casualty
        (by direct construction, not organic RNG-driven simulation — see
        module docstring for why an organic decisive-win battle can never
        satisfy this: the winning side always keeps an uncaptured survivor).
        Proves SUM(damage_dealt) across the resulting casualty rows equals
        the total damage exchanged, and SUM(kills) equals the ships actually
        destroyed."""
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

        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        a_ship = make_ship(hull=0, max_hull=100)
        d_ship = make_ship(hull=0, max_hull=100)
        a_member = make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = make_member(fleet=defender_fleet, ship=d_ship)
        svc, session = make_service(a_member, d_member)
        svc._distribute_fleet_kill_rewards = lambda *a, **k: None

        # A fires first (this round), dealing 30 non-lethal damage to D.
        # D fires back, dealing 25 (lethal, given d already softened a below
        # to 0 in a prior "round") -- both dying in THIS round's processing,
        # each having already fired before its own casualty event.
        battle = make_battle(
            attacker_fleet=attacker_fleet, defender_fleet=defender_fleet,
            battle_log=[{"results": {"shots": [
                {"ship_id": str(a_ship.id), "damage": 20, "killed": False},
                {"ship_id": str(d_ship.id), "damage": 15, "killed": False},
            ]}}],
        )
        round_results = {"shots": [
            {"ship_id": str(a_ship.id), "damage": 30, "killed": True},   # A's shot kills D
            {"ship_id": str(d_ship.id), "damage": 25, "killed": True},   # D's earlier shot (this round) kills A
        ]}

        # D recorded first (killed by A's shot).
        svc._record_ship_casualty(d_ship, battle, destroyed=True, round_results=round_results)
        # A recorded second (killed by D's return fire, same round).
        svc._record_ship_casualty(a_ship, battle, destroyed=True, round_results=round_results)

        total_damage_exchanged = 20 + 15 + 30 + 25
        total_kills_scored = 2  # A killed D, D killed A

        casualties = [o for o in session.added if isinstance(o, FleetBattleCasualty)]
        assert len(casualties) == 2
        assert sum(c.damage_dealt for c in casualties) == total_damage_exchanged
        assert sum(c.kills for c in casualties) == total_kills_scored
        assert battle.attacker_ships_destroyed == 1
        assert battle.defender_ships_destroyed == 1


# --------------------------------------------------------------------------- #
# _apply_damage_to_ship — kill-attribution return value
# --------------------------------------------------------------------------- #

class TestApplyDamageKillAttribution:
    def test_lethal_hit_returns_true_and_records_destroyed_casualty(self, monkeypatch):
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

        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        target = make_ship(hull=10, max_hull=100, shields=0)
        member = make_member(fleet=defender_fleet, ship=target)
        svc, session = make_service(member)
        svc._distribute_fleet_kill_rewards = lambda *a, **k: None

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        round_results = {"shots": [], "ships_destroyed": [], "ships_retreated": []}

        destroyed = svc._apply_damage_to_ship(target, 50, battle, round_results)

        assert destroyed is True
        assert len(round_results["ships_destroyed"]) == 1
        casualties = [o for o in session.added if isinstance(o, FleetBattleCasualty)]
        assert len(casualties) == 1
        assert casualties[0].destroyed is True

    def test_non_lethal_hit_returns_false(self):
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        target = make_ship(hull=100, max_hull=100, shields=0)
        member = make_member(fleet=defender_fleet, ship=target)
        svc, session = make_service(member)

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        round_results = {"shots": [], "ships_destroyed": [], "ships_retreated": []}

        destroyed = svc._apply_damage_to_ship(target, 10, battle, round_results)

        assert destroyed is False
        assert round_results["ships_destroyed"] == []
        assert not any(isinstance(o, FleetBattleCasualty) for o in session.added)

    def test_retreat_returns_false_not_a_kill(self, monkeypatch):
        """A retreat is a casualty event but NOT a kill for the attacker --
        _apply_damage_to_ship must return False so the firing ship's own
        "shots" entry is correctly marked killed=False."""
        monkeypatch.setattr(fs.random, "random", lambda: 0.0)  # force the 30% retreat roll to succeed

        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        target = make_ship(hull=100, max_hull=100, shields=0)
        member = make_member(fleet=defender_fleet, ship=target)
        svc, session = make_service(member)

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        round_results = {"shots": [], "ships_destroyed": [], "ships_retreated": []}

        # 80 damage drops hull to 20, which is < 30% of max_hull(100) -> retreat-eligible.
        destroyed = svc._apply_damage_to_ship(target, 80, battle, round_results)

        assert destroyed is False
        assert len(round_results["ships_retreated"]) == 1
        casualties = [o for o in session.added if isinstance(o, FleetBattleCasualty)]
        assert len(casualties) == 1
        assert casualties[0].retreated is True
        assert casualties[0].destroyed is False


# --------------------------------------------------------------------------- #
# simulate_battle_round — the real production wiring (shots ledger)
# --------------------------------------------------------------------------- #

class TestSimulateBattleRoundShotsLedger:
    def test_shots_ledger_attributes_damage_to_the_firing_ship(self, monkeypatch):
        """No deaths this round (ample hull on both sides) -- exercises ONLY
        the shots-ledger wiring inside simulate_battle_round's real fire
        loops, without touching the destroy chain at all."""
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        a_ship = make_ship(hull=1000, max_hull=1000, attack_rating=10)
        d_ship = make_ship(hull=1000, max_hull=1000, attack_rating=10)
        a_member = make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = make_member(fleet=defender_fleet, ship=d_ship)
        svc, session = make_service(a_member, d_member)

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        session._pools[FleetBattle] = [battle]

        # Deterministic RNG: every hit-chance roll succeeds (< 0.7), the only
        # possible random.choice target is unambiguous (1 ship per side), and
        # damage variance is pinned to exactly 1.0x.
        monkeypatch.setattr(fs.random, "random", lambda: 0.1)
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        result = svc.simulate_battle_round(battle.id)

        shots = result["round_results"]["shots"]
        assert len(shots) == 2
        by_ship = {s["ship_id"]: s for s in shots}
        assert by_ship[str(a_ship.id)]["damage"] == result["round_results"]["attacker_damage"]
        assert by_ship[str(a_ship.id)]["killed"] is False
        assert by_ship[str(d_ship.id)]["damage"] == result["round_results"]["defender_damage"]
        assert by_ship[str(d_ship.id)]["killed"] is False
        # Nobody died -- no casualty rows.
        assert not any(isinstance(o, FleetBattleCasualty) for o in session.added)

    def test_kill_marks_the_shot_killed_true_in_the_real_fire_loop(self, monkeypatch):
        """Attacker one-shots the defender via the REAL attacker-fire loop --
        proves _apply_damage_to_ship's return value is correctly threaded
        into round_results["shots"]["killed"] in production code, not just
        in the isolated _apply_damage_to_ship-level test above."""
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

        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        a_ship = make_ship(hull=1000, max_hull=1000, attack_rating=1000)  # overwhelming
        d_ship = make_ship(hull=1, max_hull=100)  # one-shot fragile
        a_member = make_member(fleet=attacker_fleet, ship=a_ship)
        d_member = make_member(fleet=defender_fleet, ship=d_ship)
        svc, session = make_service(a_member, d_member)
        svc._distribute_fleet_kill_rewards = lambda *a, **k: None

        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[])
        session._pools[FleetBattle] = [battle]

        monkeypatch.setattr(fs.random, "random", lambda: 0.1)
        monkeypatch.setattr(fs.random, "uniform", lambda a, b: 1.0)
        monkeypatch.setattr(fs.random, "choice", lambda seq: seq[0])

        svc.simulate_battle_round(battle.id)

        # The defender is wiped this round (its only ship dies one-shot), so
        # simulate_battle_round's return value comes from _end_battle (no
        # "round_results" key), and _end_battle appends its OWN aftermath
        # entry (no "results" key) after the round's entry -- read the
        # round entry specifically (the last one that HAS "results"),
        # mirroring what actually gets committed regardless of whether the
        # round also ends the battle.
        round_entry = next(e for e in reversed(battle.battle_log) if "results" in e)
        shots = round_entry["results"]["shots"]
        a_shot = next(s for s in shots if s["ship_id"] == str(a_ship.id))
        assert a_shot["killed"] is True

        casualties = [o for o in session.added if isinstance(o, FleetBattleCasualty)]
        assert len(casualties) == 1
        assert casualties[0].destroyed is True
        assert casualties[0].ship_id == d_ship.id
        # D never got to fire (died in the attacker phase before its own
        # return-fire phase) -- its OWN contribution is correctly zero.
        assert casualties[0].damage_dealt == 0
        assert casualties[0].kills == 0


# --------------------------------------------------------------------------- #
# Flagship succession
# --------------------------------------------------------------------------- #

class TestFlagshipSuccession:
    def test_earliest_joined_survivor_promoted(self):
        fleet = make_fleet()
        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        mid_member = make_member(
            fleet=fleet, ship=make_ship(name="Mid"), role=FleetRole.ATTACKER,
            joined_at=datetime(2026, 2, 1),
        )
        senior_member = make_member(
            fleet=fleet, ship=make_ship(name="Senior"), role=FleetRole.ATTACKER,
            joined_at=datetime(2026, 1, 15),  # earliest of the two SURVIVORS
        )
        fleet.commander_id = flagship_member.player_id

        svc, session = make_service(flagship_member, mid_member, senior_member)

        removed = svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert removed is True
        assert senior_member.role == FleetRole.FLAGSHIP.value
        assert mid_member.role == FleetRole.ATTACKER.value  # untouched
        assert fleet.commander_id == senior_member.player_id
        assert flagship_member not in session._pools[FleetMember]
        assert fleet.total_ships == 2

    def test_tie_break_lowest_member_id(self):
        fleet = make_fleet()
        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        same_time = datetime(2026, 3, 1)
        m1 = make_member(fleet=fleet, ship=make_ship(name="M1"), joined_at=same_time)
        m2 = make_member(fleet=fleet, ship=make_ship(name="M2"), joined_at=same_time)
        expected = min([m1, m2], key=lambda m: str(m.id))

        svc, session = make_service(flagship_member, m1, m2)
        svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert expected.role == FleetRole.FLAGSHIP.value
        loser = m2 if expected is m1 else m1
        assert loser.role != FleetRole.FLAGSHIP.value

    def test_commander_transfers_when_fallen_pilot_was_commander(self):
        fleet = make_fleet()
        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        successor_member = make_member(
            fleet=fleet, ship=make_ship(name="Successor"), joined_at=datetime(2026, 1, 2),
        )
        fleet.commander_id = flagship_member.player_id

        svc, session = make_service(flagship_member, successor_member)
        svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert fleet.commander_id == successor_member.player_id

    def test_non_commander_flagship_pilot_leaves_commander_unchanged(self):
        """The FLAGSHIP-role member is not always the commander (e.g. data
        drift, or a manual role reassignment) -- command must only transfer
        when the FALLEN PILOT actually held it."""
        fleet = make_fleet()
        someone_else_commander = uuid4()
        fleet.commander_id = someone_else_commander

        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        successor_member = make_member(
            fleet=fleet, ship=make_ship(name="Successor"), joined_at=datetime(2026, 1, 2),
        )

        svc, session = make_service(flagship_member, successor_member)
        svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert successor_member.role == FleetRole.FLAGSHIP.value  # succession still happens
        assert fleet.commander_id == someone_else_commander  # command untouched

    def test_last_member_destroyed_no_crash_no_promotion(self):
        """Flagship was the fleet's LAST ship -- fleet disbands; no successor
        exists, no crash, _promote_flagship_successor is never reached
        (total_ships==0 branch takes priority)."""
        fleet = make_fleet()
        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        fleet.commander_id = flagship_member.player_id

        svc, session = make_service(flagship_member)
        removed = svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert removed is True
        assert fleet.status == FleetStatus.DISBANDED.value
        assert fleet.total_ships == 0
        # Commander is left as-is (no survivor to hand it to); not this WO's
        # concern to null it out -- the fleet is disbanded either way.
        assert fleet.commander_id == flagship_member.player_id

    def test_non_flagship_removal_does_not_trigger_succession(self):
        """Regression guard: removing an ATTACKER-role member must not
        touch the flagship or commander_id at all."""
        fleet = make_fleet()
        flagship_member = make_member(
            fleet=fleet, ship=make_ship(name="Flagship"), role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        attacker_ship = make_ship(name="Attacker")
        attacker_member = make_member(fleet=fleet, ship=attacker_ship, role=FleetRole.ATTACKER)
        fleet.commander_id = flagship_member.player_id

        svc, session = make_service(flagship_member, attacker_member)
        svc.remove_ship_from_fleet(fleet.id, attacker_ship.id)

        assert flagship_member.role == FleetRole.FLAGSHIP.value
        assert fleet.commander_id == flagship_member.player_id

    def test_manual_removal_also_triggers_succession(self):
        """remove_ship_from_fleet is the SHARED path for both combat KIA and
        a plain manual removal (fleets.py route) -- succession must fire
        for a non-combat call too, with no destroy-chain involved at all."""
        fleet = make_fleet()
        flagship_ship = make_ship(name="Flagship")
        flagship_member = make_member(
            fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP,
            joined_at=datetime(2026, 1, 1),
        )
        successor_member = make_member(
            fleet=fleet, ship=make_ship(name="Successor"), joined_at=datetime(2026, 1, 2),
        )
        fleet.commander_id = flagship_member.player_id

        svc, session = make_service(flagship_member, successor_member)

        # Directly what the fleets.py route calls -- no battle, no casualty.
        result = svc.remove_ship_from_fleet(fleet.id, flagship_ship.id)

        assert result is True
        assert successor_member.role == FleetRole.FLAGSHIP.value
        assert fleet.commander_id == successor_member.player_id

    def test_promote_flagship_successor_defensive_empty_pool(self):
        """Direct unit check of the defensive guard: no remaining
        FleetMembers -> returns None, no crash (belt-and-suspenders; the
        real caller only reaches this when total_ships > 0)."""
        fleet = make_fleet()
        svc, session = make_service()  # empty FleetMember pool
        result = svc._promote_flagship_successor(fleet, fallen_pilot_id=uuid4())
        assert result is None


# --------------------------------------------------------------------------- #
# WO-MONEY-STRAGGLER-NAIVE site 3/3 -- simulate_battle_round's battle lock
# gained .populate_existing(): fleets.py:284 pre-reads `battle` UNLOCKED (to
# authorize the caller) before ever calling into this method, populating the
# session's identity map for that PK; the re-lock here is a SECOND query for
# the SAME (FleetBattle, battle_id) on the SAME session, which SQLAlchemy's
# with_for_update() alone will NOT refresh -- it hands back the stale cached
# object. That stale object feeds two things this method treats as
# authoritative: the double-simulation guard (`battle.ended_at`) and the
# round-number derivation (`len(battle.battle_log) + 1`).
# --------------------------------------------------------------------------- #

class TestSimulateBattleRoundPopulateExistingGuard:
    def test_populate_existing_precedes_with_for_update_in_source(self):
        """Structural pin (mirrors test_trading_core_pins.py's
        inspect.getsource technique): the battle-lock query must chain
        .populate_existing() before .with_for_update() -- calling it after
        (or omitting it) silently reopens the staleness hole with no
        functional-test signal in a DB-free harness (see the class below's
        own docstring on why _FakeQuery structurally can't detect this)."""
        import inspect
        source = inspect.getsource(FleetService.simulate_battle_round)
        assert ".populate_existing().with_for_update()" in source, (
            "simulate_battle_round's battle-lock query no longer chains "
            ".populate_existing() immediately before .with_for_update() -- "
            "this reopens the WO-MONEY-STRAGGLER-NAIVE site-3 stale-battle "
            "lost-update (ended_at / battle_log read stale under a "
            "concurrent double-fire)"
        )

    def test_real_sqlalchemy_identity_map_staleness_repro_and_fix(self):
        """Real SQLAlchemy engine (SQLite, StaticPool + check_same_thread=
        False so two Session() calls share one in-memory DB -- a second,
        independently-committing session simulates a genuine concurrent
        writer), mirroring test_storage_deposit_prelock_identity_map.py's
        established idiom. Real FleetBattle carries a Postgres-only
        dialects.postgresql.UUID primary key that blocks
        Base.metadata.create_all() on SQLite entirely (same precedent that
        file documents for Player) -- irrelevant here since this is pure
        SQLAlchemy identity-map/Session mechanics, independent of which
        mapped class or columns are involved. Mirrors the exact two fields
        simulate_battle_round treats as authoritative post-lock: ended_at
        (the double-simulation guard) and battle_log (round-number
        derivation) -- proves both the BROKEN shape (no populate_existing)
        and the FIX (with populate_existing, matching the real chain) in
        one row."""
        import sqlalchemy as sa
        from sqlalchemy.orm import declarative_base, sessionmaker
        from sqlalchemy.pool import StaticPool

        Base = declarative_base()

        class MirrorFleetBattle(Base):
            __tablename__ = "mirror_fleet_battles"
            id = sa.Column(sa.Integer, primary_key=True)
            ended_at = sa.Column(sa.DateTime, nullable=True)
            battle_log = sa.Column(sa.JSON, default=list)

        def session_factory():
            engine = sa.create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
            )
            Base.metadata.create_all(engine)
            return sessionmaker(bind=engine)

        # -- BROKEN shape: no populate_existing() -> stale read. -----------
        SessionFactory = session_factory()
        seed = SessionFactory()
        seed.add(MirrorFleetBattle(id=1, ended_at=None, battle_log=[{"round": 1}]))
        seed.commit()
        seed.close()

        poisoned = SessionFactory()
        # fleets.py:284's unlocked pre-read, on the same session.
        pre_check = poisoned.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1).first()
        assert pre_check.ended_at is None

        # A concurrent round-simulation genuinely ends the battle and
        # appends to the log in a DIFFERENT session/transaction.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1).first()
        row.ended_at = datetime(2026, 1, 1)
        row.battle_log = [{"round": 1}, {"round": 2}]
        concurrent.commit()
        concurrent.close()

        # The "locked" re-read -- SAME session, SAME PK, no populate_existing.
        stale = (
            poisoned.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1)
            .with_for_update().first()
        )
        assert stale is pre_check  # identity map: same cached Python object
        assert stale.ended_at is None  # STALE -- double-simulation guard blind
        assert stale.battle_log == [{"round": 1}]  # STALE -- wrong round number
        poisoned.close()

        # -- THE FIX: populate_existing() chained before with_for_update(),
        # matching fleet_service.simulate_battle_round's real chain. -------
        SessionFactory2 = session_factory()
        seed2 = SessionFactory2()
        seed2.add(MirrorFleetBattle(id=1, ended_at=None, battle_log=[{"round": 1}]))
        seed2.commit()
        seed2.close()

        fixed = SessionFactory2()
        pre_check2 = fixed.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1).first()
        assert pre_check2.ended_at is None

        concurrent2 = SessionFactory2()
        row2 = concurrent2.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1).first()
        row2.ended_at = datetime(2026, 1, 1)
        row2.battle_log = [{"round": 1}, {"round": 2}]
        concurrent2.commit()
        concurrent2.close()

        fresh = (
            fixed.query(MirrorFleetBattle).filter(MirrorFleetBattle.id == 1)
            .populate_existing().with_for_update().first()
        )
        assert fresh is pre_check2  # populate_existing refreshes in place
        assert fresh.ended_at == datetime(2026, 1, 1)  # FRESH -- guard sees it
        assert fresh.battle_log == [{"round": 1}, {"round": 2}]  # FRESH round #
        fixed.close()

    def test_double_simulation_guard_holds_through_the_real_method(self):
        """Functional (DB-free) coverage of simulate_battle_round's own
        guard, through the ACTUAL production method -- not a restatement.
        _FakeQuery has no identity map at all (it re-derives from the live,
        mutable pool on every call, see the class docstring above), so it
        cannot reproduce the staleness itself -- that's the real-SQLAlchemy
        test above's job. This proves the guard's PLAIN behavior (an ended
        battle refuses a second round) is unbroken by the populate_existing()
        chain -- the .populate_existing() no-op stub added for this WO must
        not change this method's observable outcome for the ordinary,
        uncontended path."""
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        svc, session = make_service()
        battle = make_battle(
            attacker_fleet=attacker_fleet, defender_fleet=defender_fleet, battle_log=[{"round": 1}],
        )
        battle.ended_at = datetime(2026, 1, 1)
        session._pools[FleetBattle] = [battle]

        with pytest.raises(ValueError, match="already ended"):
            svc.simulate_battle_round(battle.id)
