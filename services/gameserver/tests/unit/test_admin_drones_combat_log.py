"""Unit tests for WO-ADM-DRN-COMBAT-HISTORY (admin drone-detail combat_log
serialization).

DroneCombat.combat_log (models/drone.py) is a String(2000) JSON string written
by combat_service.py's _drone_combat_log_summary; long engagements can exceed
the column bound and get hard-truncated on write (pinned by
tests/unit/test_drone_combat_record.py::
test_attack_sector_drones_long_engagement_combat_log_fits_column), so a
truncated row is a genuine, expected runtime shape here -- not a hypothetical.

Two layers, DB-free (pattern: tests/unit/test_drone_cap_enforcement.py's
AsyncMock session):

1. `_parse_combat_log` in isolation -- the pure parse/degrade rule.
2. `get_drone_details` end-to-end against a fake AsyncSession, proving the
   new `combat_log` key sits alongside every pre-existing recent_combats key
   unchanged (id/started_at/ended_at/rounds/was_attacker/won/damage_dealt/
   damage_taken).
"""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes import admin_drones


# --- Layer 1: _parse_combat_log in isolation --------------------------------

def test_parse_combat_log_well_formed_json_returns_parsed_list():
    raw = json.dumps([{"round": 1, "tag": "sector_defense"}, {"round": 2}])

    result = admin_drones._parse_combat_log(raw)

    assert result == [{"round": 1, "tag": "sector_defense"}, {"round": 2}]


def test_parse_combat_log_truncated_json_returns_none_not_500():
    # Simulates a String(2000) hard-truncation mid-object.
    raw = '[{"round": 1, "tag": "sector_defense", "damage": 12'

    result = admin_drones._parse_combat_log(raw)

    assert result is None


def test_parse_combat_log_none_returns_none():
    assert admin_drones._parse_combat_log(None) is None


def test_parse_combat_log_non_list_json_returns_none():
    # Well-formed JSON but not the expected array shape.
    raw = json.dumps({"round": 1})

    result = admin_drones._parse_combat_log(raw)

    assert result is None


# --- Layer 2: get_drone_details end-to-end -----------------------------------

def _scalars_result(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


class _DroneDetailDb:
    """Drives get_drone_details's fixed call order: db.get(Drone) ->
    db.execute(DroneDeployment query) -> db.execute(DroneCombat query)."""

    def __init__(self, *, drone, deployments, combats):
        self.get = AsyncMock(return_value=drone)
        self.execute = AsyncMock(side_effect=[
            _scalars_result(deployments),
            _scalars_result(combats),
        ])


def _drone(drone_id):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=drone_id, player_id=uuid.uuid4(), team_id=None, drone_type="attack",
        name="Interceptor", level=3, health=80, max_health=100, attack_power=15,
        defense_power=5, speed=1.2, status="deployed", sector_id=None,
        deployed_at=now, last_action=now, kills=2, damage_dealt=120,
        damage_taken=40, battles_fought=4, abilities=None, created_at=now,
        destroyed_at=None,
    )


def _combat(*, drone_id, other_id, as_attacker, combat_log, rounds=3,
            attacker_damage_dealt=10, defender_damage_dealt=6, winner_drone_id=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        started_at=now,
        ended_at=now,
        rounds=rounds,
        attacker_drone_id=drone_id if as_attacker else other_id,
        defender_drone_id=other_id if as_attacker else drone_id,
        winner_drone_id=winner_drone_id,
        attacker_damage_dealt=attacker_damage_dealt,
        defender_damage_dealt=defender_damage_dealt,
        combat_log=combat_log,
    )


def _admin():
    return SimpleNamespace(username="admin")


@pytest.mark.asyncio
async def test_recent_combats_gain_parsed_combat_log_existing_keys_unchanged():
    drone_id = uuid.uuid4()
    other_id = uuid.uuid4()
    drone = _drone(drone_id)

    well_formed_log = [{"round": 1, "tag": "sector_defense"}]
    combat_attacker = _combat(
        drone_id=drone_id, other_id=other_id, as_attacker=True,
        combat_log=json.dumps(well_formed_log), rounds=5,
        attacker_damage_dealt=21, defender_damage_dealt=9,
        winner_drone_id=drone_id,
    )
    combat_defender_truncated = _combat(
        drone_id=drone_id, other_id=other_id, as_attacker=False,
        combat_log='[{"round": 1, "tag": "trunc', rounds=8,
        attacker_damage_dealt=14, defender_damage_dealt=30,
        winner_drone_id=other_id,
    )
    combat_no_log = _combat(
        drone_id=drone_id, other_id=other_id, as_attacker=True,
        combat_log=None, rounds=1,
        attacker_damage_dealt=0, defender_damage_dealt=0,
        winner_drone_id=None,
    )
    combats = [combat_attacker, combat_defender_truncated, combat_no_log]
    db = _DroneDetailDb(drone=drone, deployments=[], combats=combats)

    result = await admin_drones.get_drone_details(drone_id=drone_id, admin=_admin(), db=db)

    rows = result["recent_combats"]
    assert len(rows) == 3

    row0 = rows[0]
    assert row0["id"] == str(combat_attacker.id)
    assert row0["rounds"] == 5
    assert row0["was_attacker"] is True
    assert row0["won"] is True
    assert row0["damage_dealt"] == 21
    assert row0["damage_taken"] == 9
    assert row0["combat_log"] == well_formed_log

    row1 = rows[1]
    assert row1["was_attacker"] is False
    assert row1["won"] is False
    assert row1["damage_dealt"] == 30
    assert row1["damage_taken"] == 14
    assert row1["combat_log"] is None  # truncated -> degrade, no 500

    row2 = rows[2]
    assert row2["combat_log"] is None  # legacy row, never populated

    # Untouched sibling shapes: drone dict and recent_deployments still present.
    assert result["drone"]["id"] == str(drone_id)
    assert result["recent_deployments"] == []
