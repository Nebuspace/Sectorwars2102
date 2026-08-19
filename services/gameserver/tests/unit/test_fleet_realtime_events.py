"""LEG-303: fleet_status_changed / battle_round_complete / battle_ended.

fleet_moved is parked (LEG-DEC-222) — move_fleet stays retired.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.models.fleet import Fleet, FleetStatus
from src.services.fleet_service import FleetService


class _FakeQuery:
    def __init__(self, pool):
        self._pool = pool

    def filter(self, *conditions):
        return self

    def first(self):
        return None

    def delete(self):
        return 0


class _FakeSession:
    def __init__(self):
        self.commits = 0

    def query(self, model):
        return _FakeQuery([])

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass


def make_fleet() -> Fleet:
    return Fleet(
        id=uuid4(),
        team_id=uuid4(),
        name="Test Fleet",
        status=FleetStatus.READY.value,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
    )


def test_emit_fleet_event_create_task_and_swallows_no_loop():
    svc = FleetService(_FakeSession())
    svc._emit_fleet_event("fleet_status_changed", {"status": FleetStatus.DISBANDED.value})

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
    db = _FakeSession()
    svc = FleetService(db)
    svc._lock_fleets_ascending = lambda ids: {fleet.id: fleet}
    with patch.object(svc, "_emit_fleet_event") as emit:
        ok = svc.disband_fleet(fleet.id)
    assert ok is True
    assert db.commits == 1
    assert emit.call_args[0][0] == "fleet_status_changed"
    assert emit.call_args[0][1]["status"] == FleetStatus.DISBANDED.value
    assert emit.call_args[0][1]["fleet_id"] == str(fleet.id)


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


def test_event_names_wired_without_move_fleet():
    src = open("src/services/fleet_service.py", encoding="utf-8").read()
    end_round = src.split("if self._should_end_battle")[1].split("def get_battle_status")[0]
    assert end_round.index('"battle_round_complete"') < end_round.index(
        "return self._end_battle(battle)"
    )
    assert '"battle_round_complete"' in src
    assert '"fleet_status_changed"' in src
    assert '"battle_ended"' in src
    assert "def move_fleet" not in src
