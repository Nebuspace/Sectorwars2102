"""WO-BUILD-WARPGATE-BEACON-FOCUS-ATTACK-PATH — CombatService.attack_warp_beacon.

Beacon (DEPLOYED, source sector, 5k HP) was missing an attack path; Focus/
HARMONIZING+ACTIVE already covered by attack_warp_gate. DB-free fakes mirror
test_warp_gate_destruction.py conventions.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.orm.exc import StaleDataError

from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateBeaconStatus, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel
from src.services.combat_service import CombatService

_NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        right = cond.right.value if hasattr(cond.right, "value") else cond.right
        return row_val == right
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows, criteria=None, session=None, entity=None):
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity

    def filter(self, *conditions):
        return _FakeQuery(self._rows, self._criteria + list(conditions), self._session, self._entity)

    def populate_existing(self):
        return self

    def with_for_update(self):
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def _matching(self):
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self):
        matches = self._matching()
        return matches[0] if matches else None

    def all(self):
        return self._matching()

    def scalar(self):
        matches = self._matching()
        if not matches:
            return None
        row = matches[0]
        if self._entity == "Player.team_id":
            return getattr(row, "team_id", None)
        return row


class _FakeSession:
    def __init__(self, *, players=None, ships=None, gates=None, beacons=None, tunnels=None):
        self.players = players or []
        self.ships = ships if ships is not None else [
            p.current_ship for p in self.players if getattr(p, "current_ship", None) is not None
        ]
        self.gates = gates or []
        self.beacons = beacons or []
        self.tunnels = tunnels or []
        self.deleted = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.for_update_calls = []

    def query(self, *entities):
        head = entities[0]
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is Player.team_id:
            return _FakeQuery(self.players, session=self, entity="Player.team_id")
        if head is Ship:
            return _FakeQuery(self.ships, session=self, entity="Ship")
        if head is WarpGate:
            return _FakeQuery(self.gates, session=self, entity="WarpGate")
        if head is WarpGateBeacon:
            return _FakeQuery(self.beacons, session=self, entity="WarpGateBeacon")
        if head is WarpTunnel:
            return _FakeQuery(self.tunnels, session=self, entity="WarpTunnel")
        return _FakeQuery([])

    def add(self, obj):
        pass

    def delete(self, obj):
        self.deleted.append(obj)
        if obj in self.beacons:
            self.beacons.remove(obj)
        if obj in self.gates:
            self.gates.remove(obj)

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


@pytest.fixture
def freeze_now(monkeypatch):
    import src.services.combat_service as svc_module

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _NOW if tz is not None else _NOW.replace(tzinfo=None)

    monkeypatch.setattr(svc_module, "datetime", _FrozenDatetime)


def _player(**overrides):
    player_id = overrides.pop("id", None) or uuid.uuid4()
    ship_id = overrides.pop("current_ship_id", None) or uuid.uuid4()
    ship = overrides.pop("current_ship", None)
    if ship is None:
        ship = Ship(
            id=ship_id, name="Test Warship", type=ShipType.DEFENDER,
            owner_id=player_id, sector_id=42, is_destroyed=False,
            cargo={"capacity": 500, "used": 0, "contents": {}},
            combat={"shields": 100, "max_shields": 100, "hull": 200, "max_hull": 200},
        )
    base = dict(
        id=player_id, username="Raider9", current_sector_id=42,  # SOURCE default
        current_ship_id=ship_id, current_ship=ship,
        turns=1000, max_turns=1000, is_docked=False, is_landed=False,
        attack_drones=0, military_rank=None,
        last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _beacon(**overrides):
    base = dict(
        id=uuid.uuid4(), player_id=uuid.uuid4(),
        source_sector_id=42, destination_sector_id=99,
        status=WarpGateBeaconStatus.DEPLOYED,
        invulnerable_until=None, hp=5000,
        created_at=_NOW - timedelta(hours=72),
    )
    base.update(overrides)
    return WarpGateBeacon(**base)


@pytest.mark.unit
class TestBeaconAttack:
    def test_attack_reduces_beacon_hp(self, freeze_now):
        attacker = _player()
        beacon = _beacon()
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is True
        assert beacon.hp < 5000
        assert result["gate_hp_remaining"] == beacon.hp
        assert db.commit_calls == 1

    def test_destroy_deletes_beacon_and_grants_salvage(self, freeze_now):
        attacker = _player()
        beacon = _beacon(hp=1)
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is True
        assert result["destroyed"] is True
        assert beacon not in db.beacons
        assert result["salvage_granted"] == {"ore": 500, "equipment": 250, "lumen_crystals": 10}
        cargo = attacker.current_ship.cargo["contents"]
        assert cargo.get("ore") == 500

    def test_invulnerable_reject(self, freeze_now):
        attacker = _player()
        beacon = _beacon(created_at=_NOW - timedelta(hours=12))
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is False
        assert result["message"].startswith("ERR_GATE_INVULNERABLE")
        assert beacon.hp == 5000

    def test_wrong_sector_reject(self, freeze_now):
        attacker = _player(current_sector_id=99)  # destination, not source
        beacon = _beacon()
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is False
        assert "beacon's sector" in result["message"]

    def test_non_deployed_reject(self, freeze_now):
        attacker = _player()
        beacon = _beacon(status=WarpGateBeaconStatus.MATCHED)
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is False
        assert "cannot be attacked" in result["message"]

    def test_own_beacon_reject(self, freeze_now):
        attacker = _player()
        beacon = _beacon(player_id=attacker.id)
        db = _FakeSession(players=[attacker], beacons=[beacon])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is False
        assert "own" in result["message"]

    def test_existing_gate_redirects(self, freeze_now):
        attacker = _player()
        beacon = _beacon()
        gate = WarpGate(
            id=uuid.uuid4(), beacon_id=beacon.id, player_id=beacon.player_id,
            warp_tunnel_id=None, status=WarpGateStatus.HARMONIZING,
            hp=5000, harmonization_completes_at=None, anchor_ship_id=None,
            construction_cost=0,
        )
        db = _FakeSession(players=[attacker], beacons=[beacon], gates=[gate])
        result = CombatService(db).attack_warp_beacon(attacker_id=attacker.id, beacon_id=beacon.id)
        assert result["success"] is False
        assert "attack the gate instead" in result["message"]
