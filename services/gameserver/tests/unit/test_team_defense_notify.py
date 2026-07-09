"""Regression pin for WO-RT-TEAM-DEFENSE.

factions-and-teams.md "Combat advantages": "Defensive notifications when
any teammate is attacked." Before this WO nothing implemented it —
combat_service.py had no team-aware push at all. Fixed by
``_emit_teammate_under_attack``: a POST-COMMIT, best-effort
``teammate_under_attack`` broadcast to the DEFENDER's team (mirrors the
existing ``_emit_bounty_collected`` / ``_emit_combat_phase_events``
lazy-import + running-loop + create_task idiom), fired from
``CombatService.attack_player`` right after the fight commits.

This WO also culls three zero-caller ConnectionManager sender helpers
(``send_new_message_notification``, ``send_ship_status_change``,
``send_fleet_update``) that were dead weight in websocket_service.py.

Sections:
  (1)-(4) exercise ``_emit_teammate_under_attack`` directly against a FRESH
      ConnectionManager() + FakeWebSocket sockets (mirrors test_ws_room_hop.py
      / test_ws_eviction_race.py's fresh-instance pattern, so this never
      touches the process-wide singleton or other test modules' state).
  (5) source-scan: the three culled helpers are gone from websocket_service.py.

Fully DB-free and network-free throughout — attacker/defender are plain
SimpleNamespace stand-ins (the emitter only reads plain attributes, no ORM
behavior needed, matching test_combat_loot_history_nh3b.py's ``_make_player``
convention).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import types
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services import combat_service
from src.services.websocket_service import ConnectionManager


class FakeWebSocket:
    """Minimal WebSocket stand-in: records send_text() calls instead of
    touching the network (mirrors test_ws_room_hop.py / test_ws_eviction_
    race.py's FakeWebSocket)."""

    def __init__(self):
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def close(self, code=None, reason=None) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _frames(ws: FakeWebSocket) -> list[dict]:
    return [json.loads(f) for f in ws.sent]


def _player(*, team_id=None, username="pilot"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username=username,
        team_id=team_id,
    )


# --------------------------------------------------------------------------- #
# (1) Teammate receives exactly one frame, with the expected small field set.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_teammate_receives_exactly_one_frame_with_expected_fields():
    manager = ConnectionManager()
    team_id = str(uuid.uuid4())

    attacker = _player(username="raider")
    defender = _player(team_id=team_id, username="target")
    teammate_id = str(uuid.uuid4())
    teammate_ws = FakeWebSocket()

    await manager.connect(teammate_ws, teammate_id, {"username": "teammate", "team_id": team_id})
    teammate_ws.sent.clear()  # drain connect()'s own presence noise, if any

    with patch("src.services.websocket_service.connection_manager", manager):
        combat_service._emit_teammate_under_attack(attacker, defender, sector_id=42)
        await asyncio.sleep(0)  # let the scheduled create_task run

    frames = [f for f in _frames(teammate_ws) if f.get("type") == "teammate_under_attack"]
    assert len(frames) == 1
    frame = frames[0]
    assert frame["defender_id"] == str(defender.id)
    assert frame["defender_name"] == "target"
    assert frame["attacker_name"] == "raider"
    assert frame["sector_id"] == 42
    assert "timestamp" in frame


# --------------------------------------------------------------------------- #
# (2) The defender itself is excluded — they get the personal combat_update
#     frame elsewhere, not this one.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_defender_is_excluded():
    manager = ConnectionManager()
    team_id = str(uuid.uuid4())

    attacker = _player(username="raider")
    defender = _player(team_id=team_id, username="target")
    defender_ws = FakeWebSocket()

    await manager.connect(
        defender_ws, str(defender.user_id), {"username": "target", "team_id": team_id}
    )
    defender_ws.sent.clear()

    with patch("src.services.websocket_service.connection_manager", manager):
        combat_service._emit_teammate_under_attack(attacker, defender, sector_id=7)
        await asyncio.sleep(0)

    assert [f for f in _frames(defender_ws) if f.get("type") == "teammate_under_attack"] == []


# --------------------------------------------------------------------------- #
# (3) Non-team players never receive the frame (broadcast_to_team's own room
#     scoping — proven here at the emitter's call boundary).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_non_team_players_do_not_receive_the_frame():
    manager = ConnectionManager()
    team_id = str(uuid.uuid4())
    other_team_id = str(uuid.uuid4())

    attacker = _player(username="raider")
    defender = _player(team_id=team_id, username="target")

    stranger_id = str(uuid.uuid4())
    stranger_ws = FakeWebSocket()
    await manager.connect(stranger_ws, stranger_id, {"username": "stranger"})  # no team

    other_team_member_id = str(uuid.uuid4())
    other_team_ws = FakeWebSocket()
    await manager.connect(
        other_team_ws, other_team_member_id, {"username": "rival", "team_id": other_team_id}
    )

    stranger_ws.sent.clear()
    other_team_ws.sent.clear()

    with patch("src.services.websocket_service.connection_manager", manager):
        combat_service._emit_teammate_under_attack(attacker, defender, sector_id=7)
        await asyncio.sleep(0)

    assert _frames(stranger_ws) == []
    assert _frames(other_team_ws) == []


# --------------------------------------------------------------------------- #
# (4) A solo defender (team_id is None) is a silent no-op — nothing scheduled,
#     no broadcast_to_team call at all.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_solo_defender_emits_nothing():
    from unittest.mock import AsyncMock, MagicMock

    fake_cm = MagicMock()
    fake_cm.broadcast_to_team = AsyncMock()

    attacker = _player(username="raider")
    defender = _player(team_id=None, username="loner")

    with patch("src.services.websocket_service.connection_manager", fake_cm):
        combat_service._emit_teammate_under_attack(attacker, defender, sector_id=7)
        await asyncio.sleep(0)

    fake_cm.broadcast_to_team.assert_not_awaited()


# --------------------------------------------------------------------------- #
# (5) Source-scan: the three culled zero-caller helpers are gone.
# --------------------------------------------------------------------------- #

def test_culled_helpers_are_gone_from_websocket_service():
    src_path = Path(inspect.getfile(ConnectionManager))
    source = src_path.read_text(encoding="utf-8")
    for name in (
        "send_new_message_notification",
        "send_ship_status_change",
        "send_fleet_update",
    ):
        assert f"def {name}(" not in source, f"{name} should have been culled"
