"""LEG-400 — player get_battle_status exposes stored battle_log.

Pins the additive read field PC LEG-308/#697 consumes. Auth remains on the
route (participant 403); this unit covers the service payload shape only.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.fleet import FleetBattle, FleetBattleCasualty
from src.services.fleet_service import FleetService


def test_get_battle_status_includes_stored_battle_log():
    battle_id = uuid.uuid4()
    log = [
        {"round": 1, "results": {"shots": []}},
        {"round": 2, "results": {"shots": [{"hit": True}]}},
    ]
    battle = SimpleNamespace(
        id=battle_id,
        phase="engagement",
        ended_at=None,
        started_at=None,
        winner=None,
        sector_id=None,
        attacker_fleet_id=uuid.uuid4(),
        defender_fleet_id=uuid.uuid4(),
        attacker_fleet=None,
        defender_fleet=None,
        attacker_ships_initial=2,
        attacker_ships_destroyed=0,
        attacker_ships_retreated=0,
        attacker_damage_dealt=10,
        defender_ships_initial=2,
        defender_ships_destroyed=0,
        defender_ships_retreated=0,
        defender_damage_dealt=5,
        total_damage_dealt=15,
        credits_looted=0,
        battle_log=log,
    )

    db = MagicMock()

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = battle if model is FleetBattle else None
        q.all.return_value = [] if model is FleetBattleCasualty else []
        return q

    db.query.side_effect = query
    service = FleetService(db)
    result = service.get_battle_status(battle_id)

    assert result["battle_log"] == log
    assert result["rounds_completed"] == 2


def test_get_battle_status_non_list_battle_log_becomes_empty_list():
    battle_id = uuid.uuid4()
    battle = SimpleNamespace(
        id=battle_id,
        phase="engagement",
        ended_at=None,
        started_at=None,
        winner=None,
        sector_id=None,
        attacker_fleet_id=None,
        defender_fleet_id=None,
        attacker_fleet=None,
        defender_fleet=None,
        attacker_ships_initial=0,
        attacker_ships_destroyed=0,
        attacker_ships_retreated=0,
        attacker_damage_dealt=0,
        defender_ships_initial=0,
        defender_ships_destroyed=0,
        defender_ships_retreated=0,
        defender_damage_dealt=0,
        total_damage_dealt=0,
        credits_looted=0,
        battle_log=None,
    )
    db = MagicMock()

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = battle if model is FleetBattle else None
        q.all.return_value = []
        return q

    db.query.side_effect = query
    result = FleetService(db).get_battle_status(battle_id)
    assert result["battle_log"] == []
    assert result["rounds_completed"] == 0
