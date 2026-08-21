"""Unit tests for GET /combat/history (LEG-862).

Direct coroutine calls with a fake Session — no Postgres, no HTTP.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.routing import APIRoute

from src.api.routes import player_combat
from src.api.routes.player_combat import get_combat_history
from src.auth.dependencies import get_current_player
from src.models.combat_log import CombatLog
from src.models.player import Player
from src.models.sector import Sector


def _make_log(
    *,
    log_id=None,
    attacker_id,
    defender_id,
    outcome="attacker_win",
    sector_id=42,
    ended_at=None,
    timestamp=None,
    attacker_ship_name="Attacker Hull",
    defender_ship_name="Defender Hull",
):
    now = timestamp or datetime.now(timezone.utc)
    return SimpleNamespace(
        id=log_id or uuid.uuid4(),
        attacker_id=attacker_id,
        defender_id=defender_id,
        outcome=outcome,
        sector_id=sector_id,
        ended_at=ended_at,
        timestamp=now,
        attacker_ship_name=attacker_ship_name,
        defender_ship_name=defender_ship_name,
    )


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._offset = 0
        self._limit = None

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, n):
        self._offset = n
        return self

    def limit(self, n):
        self._limit = n
        return self

    def count(self):
        return len(self._rows)

    def all(self):
        sliced = self._rows[self._offset :]
        if self._limit is not None:
            sliced = sliced[: self._limit]
        return sliced

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, *, logs=None, sectors=None, players=None):
        self._logs = logs or []
        self._sectors = sectors or []
        self._players = players or []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "CombatLog":
            return _FakeQuery(self._logs)
        if name == "Sector":
            return _FakeQuery(self._sectors)
        if name == "Player":
            return _FakeQuery(self._players)
        return _FakeQuery([])


@pytest.mark.asyncio
async def test_combat_history_empty_list():
    player_id = uuid.uuid4()
    db = _FakeSession(logs=[])
    player = SimpleNamespace(id=player_id)

    response = await get_combat_history(limit=20, offset=0, player=player, db=db)

    assert response.items == []
    assert response.total == 0
    assert response.limit == 20
    assert response.offset == 0


@pytest.mark.asyncio
async def test_combat_history_attacker_sees_combat():
    player_id = uuid.uuid4()
    opponent_id = uuid.uuid4()
    log_id = uuid.uuid4()
    ended = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    log = _make_log(
        log_id=log_id,
        attacker_id=player_id,
        defender_id=opponent_id,
        ended_at=ended,
    )
    sector = SimpleNamespace(sector_id=42, name="Test Sector")
    opponent = SimpleNamespace(id=opponent_id, username="rival_pilot")
    db = _FakeSession(logs=[log], sectors=[sector], players=[opponent])
    player = SimpleNamespace(id=player_id)

    response = await get_combat_history(limit=10, offset=0, player=player, db=db)

    assert response.total == 1
    item = response.items[0]
    assert item.combatId == str(log_id)
    assert item.role == "attacker"
    assert item.outcome == "attacker_win"
    assert item.endedAt == ended.isoformat()
    assert item.opponent.id == str(opponent_id)
    assert item.opponent.displayName == "rival_pilot"
    assert item.sectorLabel == "Test Sector (42)"


@pytest.mark.asyncio
async def test_combat_history_defender_sees_combat():
    player_id = uuid.uuid4()
    attacker_id = uuid.uuid4()
    log = _make_log(attacker_id=attacker_id, defender_id=player_id)
    attacker = SimpleNamespace(id=attacker_id, username="attacker_one")
    db = _FakeSession(logs=[log], players=[attacker])
    player = SimpleNamespace(id=player_id)

    response = await get_combat_history(limit=10, offset=0, player=player, db=db)

    assert len(response.items) == 1
    assert response.items[0].role == "defender"
    assert response.items[0].opponent.displayName == "attacker_one"


@pytest.mark.asyncio
async def test_combat_history_pagination_honors_limit_offset():
    player_id = uuid.uuid4()
    logs = [
        _make_log(attacker_id=player_id, defender_id=uuid.uuid4())
        for _ in range(5)
    ]
    db = _FakeSession(logs=logs)
    player = SimpleNamespace(id=player_id)

    response = await get_combat_history(limit=2, offset=1, player=player, db=db)

    assert response.total == 5
    assert response.limit == 2
    assert response.offset == 1
    assert len(response.items) == 2


def test_combat_history_route_registered_before_status_param():
    paths = [getattr(r, "path", "") for r in player_combat.router.routes]
    history_idx = paths.index("/combat/history")
    status_idx = paths.index("/combat/{combatId}/status")
    assert history_idx < status_idx


def test_combat_history_requires_authentication():
    history_routes = [
        r
        for r in player_combat.router.routes
        if getattr(r, "path", "") == "/combat/history"
    ]
    assert len(history_routes) == 1
    route = history_routes[0]
    assert isinstance(route, APIRoute)
    dep_calls = {dep.call for dep in route.dependant.dependencies}
    assert get_current_player in dep_calls

    sig = inspect.signature(get_combat_history)
    assert "player" in sig.parameters
