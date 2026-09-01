"""LEG-939: medal_awarded personal + conditional team/sector fan-out (medal-service.md:273-280)."""
from __future__ import annotations

import uuid

import pytest

from src.services.enhanced_websocket_service import EnhancedWebSocketService
from src.services.medal_catalog import TIER_BRONZE, TIER_GOLD, TIER_PLATINUM, TIER_UNIQUE
from src.services.medal_service import (
    medal_awarded_should_broadcast_sector,
    medal_awarded_should_broadcast_team,
)
from src.services.websocket_service import ConnectionManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def close(self, code=None, reason=None) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _payload(medal_id: str = "combat.veteran") -> dict:
    return {
        "medal_id": medal_id,
        "medal_name": "Veteran",
        "medal_category": "Combat",
        "medal_tier": TIER_GOLD,
        "awarded_via": "test",
    }


# --- routing matrix helpers (no I/O) ---


@pytest.mark.parametrize(
    "tier,category,sector_id,expected",
    [
        (TIER_BRONZE, "Combat", 42, False),
        (TIER_GOLD, "Combat", 42, True),
        (TIER_UNIQUE, "Combat", 42, True),
        (TIER_PLATINUM, "Combat", 42, True),
        (TIER_BRONZE, "UNIQUE", 42, True),
        (TIER_GOLD, "Combat", None, False),
    ],
)
def test_sector_fanout_matrix(tier, category, sector_id, expected):
    assert medal_awarded_should_broadcast_sector(tier, category, sector_id) is expected


def test_team_fanout_requires_team_and_respects_mute():
    assert medal_awarded_should_broadcast_team(None, {}) is False
    assert medal_awarded_should_broadcast_team(uuid.uuid4(), {}) is True
    assert (
        medal_awarded_should_broadcast_team(
            uuid.uuid4(), {"medal_privacy": {"broadcast_to_team": False}}
        )
        is False
    )


# --- EnhancedWebSocketService integration (fresh ConnectionManager) ---


@pytest.mark.asyncio
async def test_personal_always_team_and_sector_when_qualified():
    cm = ConnectionManager()
    svc = EnhancedWebSocketService()
    svc.connection_manager = cm

    earner_uid = str(uuid.uuid4())
    teammate_uid = str(uuid.uuid4())
    sector_peer_uid = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    sector_id = 9001

    earner_ws = FakeWebSocket()
    teammate_ws = FakeWebSocket()
    sector_ws = FakeWebSocket()

    await cm.connect(
        earner_ws,
        earner_uid,
        {"username": "earner", "team_id": team_id, "current_sector": sector_id},
    )
    await cm.connect(
        teammate_ws,
        teammate_uid,
        {"username": "mate", "team_id": team_id},
    )
    await cm.connect(
        sector_ws,
        sector_peer_uid,
        {"username": "peer", "current_sector": sector_id},
    )
    earner_ws.sent.clear()
    teammate_ws.sent.clear()
    sector_ws.sent.clear()

    await svc.send_medal_awarded(
        earner_uid,
        _payload(),
        team_id=team_id,
        sector_id=sector_id,
        broadcast_team=True,
        broadcast_sector=True,
    )

    assert any("medal_awarded" in s for s in earner_ws.sent)
    assert any("medal_awarded" in s for s in teammate_ws.sent)
    assert any("medal_awarded" in s for s in sector_ws.sent)


@pytest.mark.asyncio
async def test_no_team_broadcast_when_disabled():
    cm = ConnectionManager()
    svc = EnhancedWebSocketService()
    svc.connection_manager = cm

    earner_uid = str(uuid.uuid4())
    teammate_uid = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    earner_ws = FakeWebSocket()
    teammate_ws = FakeWebSocket()
    await cm.connect(
        earner_ws,
        earner_uid,
        {"username": "earner", "team_id": team_id},
    )
    await cm.connect(
        teammate_ws,
        teammate_uid,
        {"username": "mate", "team_id": team_id},
    )
    earner_ws.sent.clear()
    teammate_ws.sent.clear()

    await svc.send_medal_awarded(
        earner_uid,
        _payload(medal_id="x"),
        team_id=team_id,
        broadcast_team=False,
        broadcast_sector=False,
    )

    assert any("medal_awarded" in s for s in earner_ws.sent)
    assert not any("medal_awarded" in s for s in teammate_ws.sent)


@pytest.mark.asyncio
async def test_earner_excluded_from_team_and_sector_duplicate():
    cm = ConnectionManager()
    svc = EnhancedWebSocketService()
    svc.connection_manager = cm

    earner_uid = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    sector_id = 7

    earner_ws = FakeWebSocket()
    await cm.connect(
        earner_ws,
        earner_uid,
        {"username": "earner", "team_id": team_id, "current_sector": sector_id},
    )
    earner_ws.sent.clear()

    await svc.send_medal_awarded(
        earner_uid,
        _payload(),
        team_id=team_id,
        sector_id=sector_id,
        broadcast_team=True,
        broadcast_sector=True,
    )

    medal_frames = [s for s in earner_ws.sent if "medal_awarded" in s]
    assert len(medal_frames) == 1
