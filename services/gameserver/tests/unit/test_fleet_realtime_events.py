"""LEG-303: fleet_moved / fleet_status_changed / battle_round_complete / battle_ended."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.models.fleet import Fleet, FleetMember, FleetRole, FleetStatus
from src.models.sector import Sector
from src.models.ship import Ship, ShipType
from src.services.fleet_service import FleetService


def _flatten(conditions):
    out = []
    for c in conditions:
        clauses = getattr(c, "get_children", None)
        if clauses and type(c).__name__ == "BooleanClauseList":
            out.extend(_flatten(c.get_children()))
        else:
            out.append(c)
    return out


def _condition_matches(row, condition):
    left = condition.left
    right = condition.right
    attr_name = left.name
    expected = right.value if hasattr(right, "value") else right
    return getattr(row, attr_name, None) == expected


class _FakeQuery:
    def __init__(self, pool):
        self._pool = pool
        self._conditions = []

    def filter(self, *conditions):
        self._conditions = self._conditions + _flatten(conditions)
        return self

    def first(self):
        matches = [r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)]
        return matches[0] if matches else None

    def delete(self):
        return 0


class _FakeSession:
    def __init__(self, pools):
        self._pools = pools
        self.commits = 0

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


def make_ship(*, sector_id=1) -> Ship:
    return Ship(
        id=uuid4(),
        name="Ship",
        type=ShipType.CARRIER,
        owner_id=uuid4(),
        sector_id=sector_id,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        maintenance={},
        cargo={},
        combat={"hull": 100, "max_hull": 100, "shields": 0, "attack_rating": 10},
    )


def make_fleet(*, sector_id=None) -> Fleet:
    return Fleet(
        id=uuid4(),
        team_id=uuid4(),
        name="Test Fleet",
        status=FleetStatus.READY.value,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
        sector_id=sector_id,
    )


def make_member(*, fleet, ship, role=FleetRole.FLAGSHIP) -> FleetMember:
    member = FleetMember(
        id=uuid4(),
        fleet_id=fleet.id,
        ship_id=ship.id if ship else None,
        player_id=uuid4(),
        role=role.value,
    )
    member.ship = ship
    member.fleet = fleet
    return member


def test_recalculate_returns_origin_destination_when_flagship_sector_changes():
    origin = uuid4()
    dest = uuid4()
    sector = Sector(id=dest, sector_id=7, name="here")
    fleet = make_fleet(sector_id=origin)
    ship = make_ship(sector_id=7)
    fleet.members = [make_member(fleet=fleet, ship=ship)]
    db = _FakeSession({Sector: [sector]})
    move = FleetService(db)._recalculate_fleet_stats(fleet)
    assert move == (origin, dest)


def test_emit_fleet_event_create_task_and_swallows_no_loop():
    svc = FleetService(_FakeSession({}))
    svc._emit_fleet_event("fleet_moved", {"origin": "a", "destination": "b"})  # no loop

    loop = MagicMock()
    with patch("asyncio.get_running_loop", return_value=loop):
        with patch("src.services.websocket_service.connection_manager") as cm:
            cm.send_fleet_event = MagicMock(return_value=MagicMock())
            svc._emit_fleet_event(
                "fleet_status_changed",
                {"status": FleetStatus.DISBANDED.value},
                team_ids=[uuid4()],
            )
    assert loop.create_task.called


def test_disband_emits_fleet_status_changed_after_commit():
    fleet = make_fleet()
    db = _FakeSession({FleetMember: []})
    svc = FleetService(db)
    svc._lock_fleets_ascending = lambda ids: {fleet.id: fleet}
    with patch.object(svc, "_emit_fleet_event") as emit:
        ok = svc.disband_fleet(fleet.id)
    assert ok is True
    assert db.commits == 1
    assert emit.call_args[0][0] == "fleet_status_changed"
    assert emit.call_args[0][1]["status"] == FleetStatus.DISBANDED.value


def test_end_battle_emits_battle_ended_once():
    started = datetime.utcnow()
    attacker = make_fleet()
    defender = make_fleet()
    battle = MagicMock()
    battle.ended_at = None
    battle.started_at = started
    battle.winner = None
    battle.credits_looted = 0
    battle.battle_log = []
    battle.phase = "engagement"
    battle.attacker_fleet = attacker
    battle.defender_fleet = defender
    battle.attacker_ships_destroyed = 0
    battle.defender_ships_destroyed = 0
    battle.attacker_ships_retreated = 0
    battle.defender_ships_retreated = 0
    battle.total_damage_dealt = 0
    battle.id = uuid4()

    db = MagicMock()
    svc = FleetService(db)
    svc._get_active_fleet_ships = lambda fleet: []
    svc._apply_battle_loot = lambda *a, **k: None
    with patch.object(svc, "_emit_fleet_event") as emit:
        first = svc._end_battle(battle)
        second = svc._end_battle(battle)
    assert first["battle_ongoing"] is False
    assert "winner" in first
    types = [c[0][0] for c in emit.call_args_list]
    assert types == ["battle_ended"]
    assert second["winner"] == first["winner"]


def test_round_complete_event_name_wired_in_simulate():
    src = open("src/services/fleet_service.py", encoding="utf-8").read()
    assert '"battle_round_complete"' in src
    assert '"fleet_moved"' in src
    assert '"fleet_status_changed"' in src
    assert '"battle_ended"' in src
    # Retired travel-as-a-unit API stays retired.
    assert "def move_fleet" not in src
