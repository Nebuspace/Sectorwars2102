"""WO-FIX-FRIENDLY-FIRE-LOCK-PARTIAL-COVERAGE — extend same-team rejection
from attack_player / fleet initiate_battle to planet, sector-drone, and
warp-gate attack entrypoints.
"""
from __future__ import annotations

import types
import uuid
from datetime import datetime, timezone

import src.services.combat_service as combat_service_module
from src.models.player import Player as PlayerModel
from src.models.planet import Planet as PlanetModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.models.station import Station as StationModel
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateStatus
from src.services.combat_service import CombatService


FRIENDLY_FIRE_PLANET = "Friendly-fire prevention: you cannot attack a teammate's planet"
FRIENDLY_FIRE_DRONES = "Friendly-fire prevention: you cannot attack a teammate's drones"
FRIENDLY_FIRE_GATE = "Friendly-fire prevention: you cannot attack a teammate's warp gate"
FRIENDLY_FIRE_PORT = "Friendly-fire prevention: you cannot attack a teammate's port"


def _make_ship(*, sector_id=1):
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = ShipType.SCOUT_SHIP
    ship.name = "Test Hull"
    ship.cargo = {"capacity": 50, "used": 0, "contents": {}}
    ship.is_destroyed = False
    ship.is_active = True
    ship.is_npc = False
    ship.current_value = 0
    ship.hangar = None
    ship.tow_state = None
    ship.sector_id = sector_id
    ship.status = ShipStatus.IN_SPACE
    ship.attack_drones = 0
    return ship


def _make_player(*, ship, team_id=None, sector_id=1):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username="pilot",
        credits=0,
        turns=999_999,
        max_turns=1_000,
        last_turn_regeneration=None,
        lifetime_turns_spent=0,
        current_ship=ship,
        current_ship_id=ship.id,
        current_sector_id=sector_id,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        attack_drones=0,
        defense_drones=0,
        military_rank="__no_such_rank__",
        personal_reputation=0,
        quantum_shards=0,
        quantum_crystals=0,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
        grey_until=None,
        grey_kind=None,
        settings={},
        team_id=team_id,
        is_suspect=False,
        suspect_until=None,
        intrasystem_pose={
            "x_pct": 50.0, "y_pct": 50.0, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


class _PlayerQueryStub:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._players.get(self._pending_id)


class _PlayerColumnQueryStub:
    def __init__(self, players_by_id, column_key):
        self._players = players_by_id
        self._column_key = column_key
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def scalar(self):
        player = self._players.get(self._pending_id)
        if player is None:
            return None
        return getattr(player, self._column_key)


class _StubQuery:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _FakeCombatDb:
    def __init__(
        self, *, players, sector=None, planet=None, station=None, drones=None, gate=None, beacon=None,
    ):
        self._players = {p.id: p for p in players}
        self._sector = sector
        self._planet = planet
        self._station = station
        self._drones = drones or []
        self._gate = gate
        self._beacon = beacon
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is PlayerModel:
            return _PlayerQueryStub(self._players)
        if model is PlayerModel.team_id:
            return _PlayerColumnQueryStub(self._players, "team_id")
        if model is SectorModel:
            return _StubQuery(first=self._sector, all_=[])
        if model is PlanetModel:
            return _StubQuery(first=self._planet, all_=[])
        if model is StationModel:
            return _StubQuery(first=self._station, all_=[])
        if model is combat_service_module.Drone:
            return _StubQuery(first=None, all_=self._drones)
        if model is WarpGate:
            return _StubQuery(first=self._gate, all_=[])
        if model is WarpGateBeacon:
            return _StubQuery(first=self._beacon, all_=[])
        if model is ShipModel:
            return _StubQuery(first=None, all_=[])
        return _StubQuery(first=None, all_=[])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


class TestIsSameTeamHelper:
    def test_same_non_null_team(self):
        team = uuid.uuid4()
        a = _make_player(ship=_make_ship(), team_id=team)
        b = _make_player(ship=_make_ship(), team_id=team)
        cs = CombatService(_FakeCombatDb(players=[a, b]))
        assert cs._is_same_team(a.id, b.id) is True

    def test_both_teamless_is_not_same_team(self):
        a = _make_player(ship=_make_ship(), team_id=None)
        b = _make_player(ship=_make_ship(), team_id=None)
        cs = CombatService(_FakeCombatDb(players=[a, b]))
        assert cs._is_same_team(a.id, b.id) is False

    def test_cross_team(self):
        a = _make_player(ship=_make_ship(), team_id=uuid.uuid4())
        b = _make_player(ship=_make_ship(), team_id=uuid.uuid4())
        cs = CombatService(_FakeCombatDb(players=[a, b]))
        assert cs._is_same_team(a.id, b.id) is False


class TestAttackPlanetFriendlyFire:
    def test_same_team_planet_blocked_zero_state_change(self, monkeypatch):
        team = uuid.uuid4()
        sector = types.SimpleNamespace(id=uuid.uuid4(), sector_id=1, region_id=None, last_combat=None)
        attacker = _make_player(ship=_make_ship(), team_id=team)
        owner = _make_player(ship=_make_ship(), team_id=team)
        planet = types.SimpleNamespace(
            id=uuid.uuid4(),
            sector_id=1,
            formation_status="formed",
            owner=[owner],
        )
        db = _FakeCombatDb(players=[attacker, owner], sector=sector, planet=planet)
        cs = CombatService(db)
        turns_before = attacker.turns

        result = cs.attack_planet(attacker_id=attacker.id, planet_id=planet.id)

        assert result["success"] is False
        assert result["message"] == FRIENDLY_FIRE_PLANET
        assert attacker.turns == turns_before
        assert db.added == []
        assert db.commits == 0


class TestAttackSectorDronesFriendlyFire:
    def test_teammate_only_drones_blocked(self, monkeypatch):
        team = uuid.uuid4()
        sector = types.SimpleNamespace(id=uuid.uuid4(), sector_id=7, region_id=None, last_combat=None)
        attacker = _make_player(ship=_make_ship(sector_id=7), team_id=team, sector_id=7)
        teammate = _make_player(ship=_make_ship(sector_id=7), team_id=team, sector_id=7)
        drone = types.SimpleNamespace(
            id=uuid.uuid4(),
            player_id=teammate.id,
            sector_id=sector.id,
            status="deployed",
            health=100,
        )
        db = _FakeCombatDb(players=[attacker, teammate], sector=sector, drones=[drone])
        cs = CombatService(db)
        turns_before = attacker.turns

        result = cs.attack_sector_drones(attacker_id=attacker.id, sector_id=7)

        assert result["success"] is False
        assert result["message"] == FRIENDLY_FIRE_DRONES
        assert attacker.turns == turns_before
        assert db.commits == 0


class TestAttackWarpGateFriendlyFire:
    def test_same_team_gate_blocked_before_attacker_lock(self):
        team = uuid.uuid4()
        attacker = _make_player(ship=_make_ship(), team_id=team)
        owner = _make_player(ship=_make_ship(), team_id=team)
        gate_id = uuid.uuid4()
        beacon_id = uuid.uuid4()
        gate = types.SimpleNamespace(
            id=gate_id,
            beacon_id=beacon_id,
            player_id=owner.id,
            status=WarpGateStatus.ACTIVE,
            hp=5000,
        )
        beacon = types.SimpleNamespace(
            id=beacon_id,
            destination_sector_id=1,
            source_sector_id=2,
        )
        db = _FakeCombatDb(players=[attacker, owner], gate=gate, beacon=beacon)
        cs = CombatService(db)
        turns_before = attacker.turns

        result = cs.attack_warp_gate(attacker_id=attacker.id, gate_id=gate_id)

        assert result["success"] is False
        assert result["message"] == FRIENDLY_FIRE_GATE
        assert attacker.turns == turns_before
        assert db.commits == 0


class TestAttackPortFriendlyFire:
    def test_same_team_port_blocked_zero_state_change(self):
        team = uuid.uuid4()
        sector = types.SimpleNamespace(id=uuid.uuid4(), sector_id=1, region_id=None, last_combat=None)
        attacker = _make_player(ship=_make_ship(), team_id=team)
        owner = _make_player(ship=_make_ship(), team_id=team)
        station = types.SimpleNamespace(
            id=uuid.uuid4(),
            sector_id=1,
            owner=[owner],
        )
        db = _FakeCombatDb(players=[attacker, owner], sector=sector, station=station)
        cs = CombatService(db)
        turns_before = attacker.turns

        result = cs.attack_port(attacker_id=attacker.id, station_id=station.id)

        assert result["success"] is False
        assert result["message"] == FRIENDLY_FIRE_PORT
        assert attacker.turns == turns_before
        assert db.added == []
        assert db.commits == 0
