"""LEG-338: gameplay track_activity + durable PlayerActivity mirror (DB-free)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.player_analytics import PlayerActivity
from src.services.player_activity_service import (
    ActivityEventType,
    PlayerActivityService,
)


class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeSession:
    def __init__(self) -> None:
        self.added: List[Any] = []

    def begin_nested(self) -> _Nested:
        return _Nested()

    def add(self, obj: Any) -> None:
        self.added.append(obj)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        ActivityEventType.COMBAT_ATTACK,
        ActivityEventType.COMBAT_DEFEND,
        ActivityEventType.SECTOR_MOVE,
        ActivityEventType.DOCK,
        ActivityEventType.UNDOCK,
        ActivityEventType.PLANET_LAND,
        ActivityEventType.WARP,
    ],
)
async def test_track_activity_writes_redis_and_durable_row(event_type: str):
    redis = MagicMock()
    redis.cache_get = AsyncMock(
        return_value={
            "actions_count": 0,
            "sectors_visited": [],
            "db_session_id": str(uuid.uuid4()),
        }
    )
    redis.cache_set = AsyncMock()
    redis.redis_pool = None

    db = _FakeSession()
    svc = PlayerActivityService(redis=redis)
    with patch.object(svc, "_record_event", new=AsyncMock()) as record:
        await svc.track_activity(
            str(uuid.uuid4()),
            event_type,
            {"sector_id": 42, "travel_mode": "warp"},
            db=db,
        )
        record.assert_awaited_once()

    redis.cache_set.assert_awaited()
    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, PlayerActivity)
    assert row.activity_type == event_type
    assert row.sector_id == 42


@pytest.mark.asyncio
async def test_trade_path_still_durably_inserts():
    redis = MagicMock()
    redis.cache_get = AsyncMock(return_value={"actions_count": 0, "trades_count": 0, "trade_volume": 0})
    redis.cache_set = AsyncMock()
    redis.redis_pool = None
    db = _FakeSession()
    svc = PlayerActivityService(redis=redis)
    with patch.object(svc, "_record_event", new=AsyncMock()):
        await svc.track_activity(
            str(uuid.uuid4()),
            ActivityEventType.TRADE_BUY,
            {"total_value": 100, "commodity": "ore", "quantity": 2, "sector_id": 7},
            db=db,
        )
    assert len(db.added) == 1
    assert db.added[0].activity_type == ActivityEventType.TRADE_BUY
    assert db.added[0].credits_involved == 100


@pytest.mark.asyncio
async def test_no_durable_row_without_db():
    redis = MagicMock()
    redis.cache_get = AsyncMock(return_value={"actions_count": 0, "sectors_visited": []})
    redis.cache_set = AsyncMock()
    redis.redis_pool = None
    svc = PlayerActivityService(redis=redis)
    with patch.object(svc, "_record_event", new=AsyncMock()) as record:
        await svc.track_activity(
            str(uuid.uuid4()),
            ActivityEventType.SECTOR_MOVE,
            {"sector_id": 1},
            db=None,
        )
        record.assert_awaited_once()
    redis.cache_set.assert_awaited()


def test_gameplay_routes_call_track_activity():
    """Accept pin: combat/move/dock/undock/land/warp call sites invoke track_activity."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src"
    expected = {
        "api/routes/player_combat.py": "COMBAT_ATTACK",
        "api/routes/player.py": "SECTOR_MOVE",
        "api/routes/trading.py": "DOCK",
        "api/routes/planets.py": "PLANET_LAND",
    }
    for rel, token in expected.items():
        text = (root / rel).read_text()
        assert "track_activity" in text, f"{rel} missing track_activity"
        assert token in text, f"{rel} missing {token}"
    trading = (root / "api/routes/trading.py").read_text()
    assert "UNDOCK" in trading
    player = (root / "api/routes/player.py").read_text()
    assert "WARP" in player
    combat = (root / "api/routes/player_combat.py").read_text()
    assert "COMBAT_DEFEND" in combat
