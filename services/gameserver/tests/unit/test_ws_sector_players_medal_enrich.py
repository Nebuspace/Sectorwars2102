"""LEG-2654 — WS sector_players medal identity enrich (mirrors REST enrich)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services.websocket_service import (
    ConnectionManager,
    connection_manager,
    enrich_ws_sector_players_medal_identity,
    handle_websocket_message,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_a, **_k):
        return self

    def group_by(self, *_a, **_k):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, *, players=None, medal_counts=None):
        self._players = players or []
        self._medal_counts = medal_counts or []

    def query(self, *entities):
        if len(entities) != 1:
            return _FakeQuery(self._medal_counts)
        model = entities[0]
        name = getattr(model, "__name__", "")
        if name == "Player":
            return _FakeQuery(self._players)
        return _FakeQuery([])


def _player_row(*, pin: str, show_count: bool = True, medal_count: int = 0):
    pid = uuid.uuid4()
    return (
        SimpleNamespace(
            id=pid,
            settings={
                "medal_privacy": {
                    "pinned_medal_id": pin,
                    "show_count_publicly": show_count,
                }
            },
        ),
        str(pid),
        medal_count,
    )


@pytest.fixture(autouse=True)
def _isolate_connection_manager(monkeypatch):
    """Tests must not mutate the process-global connection_manager."""
    cm = ConnectionManager()
    monkeypatch.setattr(
        "src.services.websocket_service.connection_manager",
        cm,
    )
    return cm


def test_enrich_includes_pinned_medal_id_when_player_has_pin(_isolate_connection_manager):
    cm = _isolate_connection_manager
    player, pid, medal_count = _player_row(pin="bronze_cluster", medal_count=3)
    user_id = "user-1"
    cm.sector_connections[7] = {user_id}
    cm.connection_metadata[user_id] = {
        "user_data": {"username": "pilot", "player_id": pid, "reputation_tier": "Neutral"},
    }

    sparse = cm.get_sector_players(7)
    session = _FakeSession(players=[player], medal_counts=[(player.id, medal_count)])
    enriched = enrich_ws_sector_players_medal_identity(session, sparse)

    assert enriched[0]["pinned_medal_id"] == "bronze_cluster"
    assert enriched[0]["medal_count"] == 3


def test_enrich_hides_medal_count_when_show_count_publicly_false(_isolate_connection_manager):
    cm = _isolate_connection_manager
    player, pid, _ = _player_row(pin="x", show_count=False, medal_count=9)
    user_id = "user-2"
    cm.sector_connections[1] = {user_id}
    cm.connection_metadata[user_id] = {
        "user_data": {"username": "quiet", "player_id": pid, "reputation_tier": "Neutral"},
    }

    sparse = cm.get_sector_players(1)
    session = _FakeSession(players=[player], medal_counts=[(player.id, 9)])
    enriched = enrich_ws_sector_players_medal_identity(session, sparse)

    assert enriched[0]["pinned_medal_id"] == "x"
    assert enriched[0]["medal_count"] is None


@pytest.mark.asyncio
async def test_request_sector_players_frame_carries_medal_fields(
    _isolate_connection_manager, monkeypatch
):
    cm = _isolate_connection_manager
    requester = "req-user"
    peer = "peer-user"
    player, pid, medal_count = _player_row(pin="silver_star", medal_count=2)

    cm.sector_connections[42] = {requester, peer}
    cm.connection_metadata[requester] = {
        "current_sector": 42,
        "user_data": {"username": "me", "player_id": str(uuid.uuid4()), "reputation_tier": "Neutral"},
    }
    cm.connection_metadata[peer] = {
        "user_data": {
            "username": "peer",
            "player_id": pid,
            "reputation_tier": "Heroic",
            "personal_reputation": 100,
        },
    }

    sent: list = []

    async def _capture(_uid, payload):
        sent.append(payload)

    monkeypatch.setattr(cm, "send_personal_message", _capture)

    class _SessionCtx:
        def __enter__(self):
            return _FakeSession(players=[player], medal_counts=[(player.id, medal_count)])

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(
        "src.core.database.SessionLocal",
        lambda: _SessionCtx(),
    )

    await handle_websocket_message(requester, {"type": "request_sector_players"})

    assert len(sent) == 1
    frame = sent[0]
    assert frame["type"] == "sector_players"
    peer_row = next(p for p in frame["players"] if p["user_id"] == peer)
    assert peer_row["pinned_medal_id"] == "silver_star"
    assert peer_row["medal_count"] == 2
