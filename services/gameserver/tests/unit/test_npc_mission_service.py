"""Unit tests — npc_mission_service.py (colonist couriers + science vessels).

No test file existed for this service (found during a zero-caller/coverage
sweep). DB-free, following this suite's hand-rolled-fake convention (see
test_npc_quantum_drops.py) rather than standing up a real DB: Planet's/Ship's
Postgres-only column types (UUID/JSONB/ARRAY) don't compile on SQLite, so a
lightweight ``_SeqFakeSession`` returns query results in the exact call
order the function under test performs them (each entry pre-composed as if
the SQL WHERE clause already ran) — the interesting, bug-prone logic here is
the route-building/reachability/capacity math in this module, not the
declarative filter clauses themselves.

Sections:
  TestPopulationHub / TestGenerateColonistRoute / TestGenerateScienceRoute —
    route generation, with ``_reachable_sectors`` monkeypatched to a fixed
    reachable-set (its own BFS lives in npc_engagement_service, out of
    scope here).
  TestBuildMissionSchedule — pure function, day/block shape for routes of
    varying length.
  TestCargoColonistsHelpers — the cargo dict read/write helpers in
    isolation, using a real (unattached) Ship instance so flag_modified
    behaves correctly.
  TestRunMissionStop — load/deliver/survey through run_mission_stop,
    covering the capacity/room/COLONIST_DROP clamps and the early-return
    guards (no ship, no capacity, wrong sector, nothing carried).
"""
import uuid

import src.services.npc_mission_service as mission_module
from src.models.planet import Planet
from src.models.ship import Ship
from src.services.npc_mission_service import (
    COLONIST_DROP,
    COLONIST_STOPS,
    SCIENCE_STOPS,
    _cargo_colonists,
    _set_cargo_colonists,
    build_mission_schedule,
    generate_colonist_route,
    generate_science_route,
    run_mission_stop,
)


class _SeqFakeSession:
    """Returns pre-composed query results in call order; ignores filter
    args (the caller composes each entry as if the WHERE clause already
    ran)."""

    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def query(self, _model):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        r = self._results[self._i]
        self._i += 1
        return r

    def all(self):
        r = self._results[self._i]
        self._i += 1
        return r

    def flush(self):
        pass


def _planet(*, sector_id, population=0, max_population=1000, is_hub=False, name="P"):
    p = Planet()
    p.id = uuid.uuid4()
    p.sector_id = sector_id
    p.population = population
    p.max_population = max_population
    p.is_population_hub = is_hub
    p.name = name
    return p


def _ship(*, capacity=1000, contents=None):
    s = Ship()
    s.id = uuid.uuid4()
    s.cargo = {"capacity": capacity, "used": 0, "contents": dict(contents or {})}
    return s


# ---------------------------------------------------------------------------
# generate_colonist_route
# ---------------------------------------------------------------------------


class TestGenerateColonistRoute:
    def test_no_hub_returns_none(self):
        db = _SeqFakeSession([None])
        assert generate_colonist_route(db, home_sector_id=1) is None

    def test_no_reachable_sectors_returns_none(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {})
        db = _SeqFakeSession([hub])
        assert generate_colonist_route(db, home_sector_id=1) is None

    def test_no_candidate_targets_returns_none(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        monkeypatch.setattr(
            mission_module, "_reachable_sectors", lambda db, sid: {2: 1, 3: 2}
        )
        db = _SeqFakeSession([hub, []])
        assert generate_colonist_route(db, home_sector_id=1) is None

    def test_hub_itself_excluded_even_if_reachable(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        # A candidate list that (incorrectly, as a real WHERE clause never
        # would) includes the hub's own sector -- the route builder must
        # still exclude it explicitly.
        same_sector_as_hub = _planet(sector_id=1, population=0)
        monkeypatch.setattr(
            mission_module, "_reachable_sectors", lambda db, sid: {1: 0}
        )
        db = _SeqFakeSession([hub, [same_sector_as_hub]])
        assert generate_colonist_route(db, home_sector_id=1) is None

    def test_route_starts_with_a_load_stop_at_the_hub(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        target = _planet(sector_id=2, population=10, max_population=100)
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {2: 1})
        db = _SeqFakeSession([hub, [target]])
        route = generate_colonist_route(db, home_sector_id=1)
        assert route[0] == {
            "sector_id": hub.sector_id,
            "planet_id": str(hub.id),
            "action": "load",
        }
        assert route[1]["action"] == "deliver"
        assert route[1]["planet_id"] == str(target.id)

    def test_caps_deliver_stops_at_colonist_stops(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        targets = [_planet(sector_id=100 + i, population=0) for i in range(COLONIST_STOPS + 5)]
        monkeypatch.setattr(
            mission_module,
            "_reachable_sectors",
            lambda db, sid: {p.sector_id: 1 for p in targets},
        )
        db = _SeqFakeSession([hub, targets])
        route = generate_colonist_route(db, home_sector_id=1)
        deliver_stops = [s for s in route if s["action"] == "deliver"]
        assert len(deliver_stops) == COLONIST_STOPS

    def test_targets_outside_reach_are_excluded(self, monkeypatch):
        hub = _planet(sector_id=1, is_hub=True)
        near = _planet(sector_id=2, population=0)
        far = _planet(sector_id=999, population=0)
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {2: 1})
        db = _SeqFakeSession([hub, [near, far]])
        route = generate_colonist_route(db, home_sector_id=1)
        planet_ids = {s["planet_id"] for s in route}
        assert str(near.id) in planet_ids
        assert str(far.id) not in planet_ids


# ---------------------------------------------------------------------------
# generate_science_route
# ---------------------------------------------------------------------------


class TestGenerateScienceRoute:
    def test_no_reachable_sectors_returns_none(self, monkeypatch):
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {})
        db = _SeqFakeSession([])
        assert generate_science_route(db, home_sector_id=1) is None

    def test_no_candidates_returns_none(self, monkeypatch):
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {2: 1})
        db = _SeqFakeSession([[]])
        assert generate_science_route(db, home_sector_id=1) is None

    def test_caps_survey_stops_at_science_stops(self, monkeypatch):
        targets = [_planet(sector_id=100 + i, population=0) for i in range(SCIENCE_STOPS + 3)]
        monkeypatch.setattr(
            mission_module,
            "_reachable_sectors",
            lambda db, sid: {p.sector_id: 1 for p in targets},
        )
        db = _SeqFakeSession([targets])
        route = generate_science_route(db, home_sector_id=1)
        assert len(route) == SCIENCE_STOPS
        assert all(s["action"] == "survey" for s in route)

    def test_unreachable_candidates_excluded(self, monkeypatch):
        near = _planet(sector_id=2, population=0)
        far = _planet(sector_id=999, population=0)
        monkeypatch.setattr(mission_module, "_reachable_sectors", lambda db, sid: {2: 1})
        db = _SeqFakeSession([[near, far]])
        route = generate_science_route(db, home_sector_id=1)
        planet_ids = {s["planet_id"] for s in route}
        assert planet_ids == {str(near.id)}


# ---------------------------------------------------------------------------
# build_mission_schedule
# ---------------------------------------------------------------------------


class TestBuildMissionSchedule:
    def test_two_days_per_stop(self):
        route = [
            {"sector_id": 1, "planet_id": "p1", "action": "load"},
            {"sector_id": 2, "planet_id": "p2", "action": "deliver"},
        ]
        schedule = build_mission_schedule(route, "colonist")
        assert schedule["route_cycle"]["cycle_days"] == 4
        assert set(schedule["route_cycle"]["days"].keys()) == {"0", "1", "2", "3"}

    def test_mission_and_route_stored_verbatim(self):
        route = [{"sector_id": 5, "planet_id": "p5", "action": "survey"}]
        schedule = build_mission_schedule(route, "science")
        assert schedule["mission"] == "science"
        assert schedule["mission_route"] == route

    def test_transit_day_commutes_to_stop_sector(self):
        route = [{"sector_id": 7, "planet_id": "p7", "action": "deliver"}]
        schedule = build_mission_schedule(route, "colonist")
        transit_day = schedule["route_cycle"]["days"]["0"]
        commute_block = next(b for b in transit_day if b["activity"] == "commute")
        assert commute_block["location_ref"] == {"sector_id": 7}

    def test_action_day_carries_stop_index_and_action(self):
        route = [
            {"sector_id": 1, "planet_id": "p1", "action": "load"},
            {"sector_id": 2, "planet_id": "p2", "action": "deliver"},
        ]
        schedule = build_mission_schedule(route, "colonist")
        second_action_day = schedule["route_cycle"]["days"]["3"]
        work_block = next(b for b in second_action_day if b["activity"] == "work_station")
        assert work_block["location_ref"]["action"] == "deliver"
        assert work_block["location_ref"]["stop_index"] == 1

    def test_empty_route_yields_empty_schedule(self):
        schedule = build_mission_schedule([], "colonist")
        assert schedule["route_cycle"]["cycle_days"] == 0
        assert schedule["route_cycle"]["days"] == {}


# ---------------------------------------------------------------------------
# cargo helpers
# ---------------------------------------------------------------------------


class TestCargoColonistsHelpers:
    def test_reads_zero_when_no_colonists(self):
        ship = _ship()
        assert _cargo_colonists(ship) == 0

    def test_reads_existing_colonist_count(self):
        ship = _ship(contents={"colonists": 42})
        assert _cargo_colonists(ship) == 42

    def test_set_positive_value_populates_contents_and_used(self):
        ship = _ship(contents={"ore": 10})
        _set_cargo_colonists(ship, 30)
        assert ship.cargo["contents"]["colonists"] == 30
        assert ship.cargo["used"] == 40

    def test_set_zero_removes_colonists_key(self):
        ship = _ship(contents={"colonists": 30, "ore": 5})
        _set_cargo_colonists(ship, 0)
        assert "colonists" not in ship.cargo["contents"]
        assert ship.cargo["used"] == 5


# ---------------------------------------------------------------------------
# run_mission_stop
# ---------------------------------------------------------------------------


def _npc(*, ship_id, current_sector_id=1, display_name="NPC"):
    import types

    return types.SimpleNamespace(
        id=uuid.uuid4(),
        ship_id=ship_id,
        current_sector_id=current_sector_id,
        display_name=display_name,
    )


class TestRunMissionStopLoad:
    def test_no_ship_returns_no_events(self):
        npc = _npc(ship_id=None)
        db = _SeqFakeSession([])
        events = run_mission_stop(db, npc, {"action": "load"})
        assert events == []

    def test_loads_up_to_full_capacity_when_empty(self):
        ship = _ship(capacity=500)
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "load"})
        assert _cargo_colonists(ship) == 500
        assert events[0]["type"] == "npc_colonists_loaded"
        assert events[0]["amount"] == 500

    def test_zero_capacity_ship_loads_nothing(self):
        ship = _ship(capacity=0)
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "load"})
        assert events == []
        assert _cargo_colonists(ship) == 0

    def test_other_cargo_reduces_available_room(self):
        ship = _ship(capacity=500, contents={"ore": 300})
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "load"})
        assert events[0]["amount"] == 200
        assert _cargo_colonists(ship) == 200

    def test_no_room_left_loads_nothing(self):
        ship = _ship(capacity=500, contents={"ore": 500})
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "load"})
        assert events == []


class TestRunMissionStopDeliver:
    def test_nothing_carried_returns_no_events(self):
        ship = _ship()
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(uuid.uuid4())})
        assert events == []

    def test_planet_not_in_current_sector_delivers_nothing(self):
        ship = _ship(contents={"colonists": 100})
        planet = _planet(sector_id=99, population=0, max_population=1000)
        npc = _npc(ship_id=ship.id, current_sector_id=1)
        db = _SeqFakeSession([ship, planet])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(planet.id)})
        assert events == []
        assert _cargo_colonists(ship) == 100  # unchanged

    def test_delivers_full_carried_amount_when_room_and_cap_allow(self):
        ship = _ship(contents={"colonists": 100})
        planet = _planet(sector_id=1, population=0, max_population=1000)
        npc = _npc(ship_id=ship.id, current_sector_id=1)
        db = _SeqFakeSession([ship, planet])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(planet.id)})
        assert planet.population == 100
        assert _cargo_colonists(ship) == 0
        assert events[0] == {
            "type": "npc_colonists_delivered",
            "sector_id": 1,
            "npc_id": str(npc.id),
            "planet_id": str(planet.id),
            "amount": 100,
        }

    def test_delivery_clamped_by_remaining_planet_capacity(self):
        ship = _ship(contents={"colonists": 500})
        planet = _planet(sector_id=1, population=980, max_population=1000)
        npc = _npc(ship_id=ship.id, current_sector_id=1)
        db = _SeqFakeSession([ship, planet])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(planet.id)})
        assert planet.population == 1000
        assert _cargo_colonists(ship) == 480  # 500 - 20 delivered
        assert events[0]["amount"] == 20

    def test_delivery_clamped_by_colonist_drop_ceiling(self):
        ship = _ship(contents={"colonists": COLONIST_DROP + 500})
        planet = _planet(sector_id=1, population=0, max_population=100000)
        npc = _npc(ship_id=ship.id, current_sector_id=1)
        db = _SeqFakeSession([ship, planet])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(planet.id)})
        assert events[0]["amount"] == COLONIST_DROP
        assert planet.population == COLONIST_DROP

    def test_planet_already_at_max_population_delivers_nothing(self):
        ship = _ship(contents={"colonists": 100})
        planet = _planet(sector_id=1, population=1000, max_population=1000)
        npc = _npc(ship_id=ship.id, current_sector_id=1)
        db = _SeqFakeSession([ship, planet])
        events = run_mission_stop(db, npc, {"action": "deliver", "planet_id": str(planet.id)})
        assert events == []
        assert _cargo_colonists(ship) == 100  # unchanged


class TestRunMissionStopSurvey:
    def test_survey_emits_event_without_mutating_ship(self):
        ship = _ship(contents={"colonists": 7})
        npc = _npc(ship_id=ship.id, current_sector_id=3)
        db = _SeqFakeSession([ship])
        events = run_mission_stop(db, npc, {"action": "survey"})
        assert events == [{
            "type": "npc_survey",
            "sector_id": 3,
            "npc_id": str(npc.id),
        }]
        assert _cargo_colonists(ship) == 7  # unchanged


class TestRunMissionStopUnknownAction:
    def test_unknown_action_returns_no_events(self):
        ship = _ship()
        npc = _npc(ship_id=ship.id)
        db = _SeqFakeSession([ship])
        assert run_mission_stop(db, npc, {"action": "loiter"}) == []
