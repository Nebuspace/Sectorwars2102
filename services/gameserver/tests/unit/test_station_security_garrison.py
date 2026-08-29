"""LEG-299 — STATION_SECURITY garrison from NPCBarracks + Station.security JSONB.

DB-free: FakeSession maps Station / NPCBarracks / NPCCharacter / ShipSpecification.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
os.environ.setdefault("DATABASE_URL", "postgresql://ci:ci@127.0.0.1:1/ci")

from src.models.npc_barracks import NPCBarracks, NPCLodgingLocationType
from src.models.npc_character import NPCArchetype, NPCCharacter
from src.models.ship import Ship, ShipSpecification, ShipType
from src.models.station import Station
from src.services.station_security_garrison import (
    ensure_garrison_from_barracks,
    ensure_station_security_garrison,
    garrison_spec_for_tier,
)


def _spec(ship_type: ShipType) -> SimpleNamespace:
    return SimpleNamespace(
        type=ship_type,
        speed=10,
        turn_cost=1,
        warp_compatible=True,
        max_cargo=50,
        max_shields=40,
        shield_recharge_rate=1,
        hull_points=80,
        evasion=5,
        attack_rating=10,
        defense_rating=10,
        attack_turn_cost=1,
        shield_resistance=0.0,
        armor_rating=0.0,
        max_genesis_devices=0,
        max_drones=0,
    )


class _FakeQuery:
    def __init__(self, result, *, many=None):
        self._result = result
        self._many = many if many is not None else ([] if result is None else [result])

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return list(self._many)


class _FakeSession:
    def __init__(self, *, station, barracks=None, npcs=None, specs=None):
        self.station = station
        self.barracks = barracks
        self.npcs = list(npcs or [])
        self.specs = specs or {
            ShipType.LIGHT_FREIGHTER: _spec(ShipType.LIGHT_FREIGHTER),
            ShipType.DEFENDER: _spec(ShipType.DEFENDER),
        }
        self.added = []
        self.flush_calls = 0
        self._spec_calls = 0

    def query(self, model):
        if model is Station:
            return _FakeQuery(self.station)
        if model is NPCBarracks:
            return _FakeQuery(self.barracks)
        if model is NPCCharacter:
            return _FakeQuery(self.npcs[0] if self.npcs else None, many=self.npcs)
        if model is ShipSpecification:
            return _SpecQuery(self)
        raise AssertionError(f"unexpected query {model}")

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, NPCBarracks):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.barracks = obj
        if isinstance(obj, NPCCharacter):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.npcs.append(obj)
        if isinstance(obj, Ship) and obj.id is None:
            obj.id = uuid.uuid4()

    def flush(self):
        self.flush_calls += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


class _SpecQuery:
    def __init__(self, session: _FakeSession):
        self._session = session

    def filter(self, *a, **k):
        return self

    def first(self):
        # Premium spawn order: 4 Light Freighter then 1 Defender.
        n = self._session._spec_calls
        self._session._spec_calls += 1
        if n >= 4:
            return self._session.specs[ShipType.DEFENDER]
        return self._session.specs[ShipType.LIGHT_FREIGHTER]


def _station(*, tier: str) -> Station:
    s = Station()
    s.id = uuid.uuid4()
    s.name = "Garrison Test Port"
    s.sector_id = 2551
    s.region_id = uuid.uuid4()
    s.faction_affiliation = "terran_federation"
    s.security = {"tier": tier}
    return s


def test_roster_table_matches_canon():
    assert garrison_spec_for_tier("basic") == (0, 0, 0)
    assert garrison_spec_for_tier("standard") == (2, 0, 2)
    assert garrison_spec_for_tier("premium") == (4, 1, 5)
    assert garrison_spec_for_tier("none") == (0, 0, 0)


def test_basic_clears_roster_without_spawn():
    station = _station(tier="basic")
    db = _FakeSession(station=station)
    out = ensure_station_security_garrison(db, station)
    assert out["guard_npc_ids"] == []
    assert out["guard_captain_npc_id"] is None
    assert out["spawned"] is False
    assert not any(isinstance(x, NPCCharacter) for x in db.added)


def test_standard_spawns_two_station_security_guards():
    station = _station(tier="standard")
    db = _FakeSession(station=station)
    out = ensure_station_security_garrison(db, station)
    assert len(out["guard_npc_ids"]) == 2
    assert out["guard_captain_npc_id"] is None
    assert out["barracks_id"]
    npcs = [x for x in db.added if isinstance(x, NPCCharacter)]
    assert len(npcs) == 2
    assert all(n.archetype == NPCArchetype.STATION_SECURITY for n in npcs)
    ships = [x for x in db.added if isinstance(x, Ship)]
    assert len(ships) == 2
    assert all(s.type == ShipType.LIGHT_FREIGHTER for s in ships)
    assert station.security["barracks_id"] == out["barracks_id"]


def test_premium_spawns_four_guards_and_captain_on_defender():
    station = _station(tier="premium")
    db = _FakeSession(station=station)
    out = ensure_station_security_garrison(db, station)
    assert len(out["guard_npc_ids"]) == 4
    assert out["guard_captain_npc_id"]
    assert out["guard_captain_npc_id"] not in out["guard_npc_ids"]
    npcs = [x for x in db.added if isinstance(x, NPCCharacter)]
    assert len(npcs) == 5
    captains = [n for n in npcs if n.duty_role == "station_security_captain"]
    assert len(captains) == 1
    ships = {id(n): n.ship_id for n in npcs}
    defender_ships = [x for x in db.added if isinstance(x, Ship) and x.type == ShipType.DEFENDER]
    assert len(defender_ships) == 1
    assert captains[0].ship_id == defender_ships[0].id


def test_standard_idempotent_second_call():
    station = _station(tier="standard")
    db = _FakeSession(station=station)
    first = ensure_station_security_garrison(db, station)
    n_first = len([x for x in db.added if isinstance(x, NPCCharacter)])
    second = ensure_station_security_garrison(db, station)
    n_second = len([x for x in db.added if isinstance(x, NPCCharacter)])
    assert n_first == n_second == 2
    assert first["guard_npc_ids"] == second["guard_npc_ids"]


def test_existing_barracks_row_spawns_via_station_lookup():
    station = _station(tier="standard")
    barracks = NPCBarracks(
        id=uuid.uuid4(),
        name="Existing",
        location_type=NPCLodgingLocationType.STATION,
        station_id=station.id,
        home_region_id=station.region_id,
        faction_code="terran_federation",
        archetype=NPCArchetype.STATION_SECURITY,
        capacity=2,
        assigned_npc_ids=[],
        current_occupants_count=0,
    )
    db = _FakeSession(station=station, barracks=barracks)
    out = ensure_garrison_from_barracks(db, barracks)
    assert out is not None
    assert len(out["guard_npc_ids"]) == 2
    assert all(n.archetype == NPCArchetype.STATION_SECURITY for n in db.npcs)
