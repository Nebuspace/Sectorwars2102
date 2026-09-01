"""LEG-2575 — personal:{user_id} ownership gate on subscribe_topic.

Canon: SYSTEMS/realtime-bus.md single-recipient invariant. Cross-user
personal: subscribe must be rejected (subscription_rejected) and logged as
a security event; owner subscribe still works; publish_topic must not
deliver to a denied attacker.
"""
from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.services.websocket_service import (
    MAX_TOPICS_PER_USER,
    ConnectionManager,
    handle_websocket_message,
    is_personal_topic_forbidden,
    personal_topic_owner,
)


class FakeWebSocket:
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


@pytest.fixture(autouse=True)
def _clean_singleton_registries():
    from src.services import websocket_service as ws_mod

    mgr = ws_mod.connection_manager
    mgr.active_connections.clear()
    mgr.connection_metadata.clear()
    mgr.topic_subscriptions.clear()
    yield
    mgr.active_connections.clear()
    mgr.connection_metadata.clear()
    mgr.topic_subscriptions.clear()


def test_personal_topic_owner_helpers():
    assert personal_topic_owner("market:ore") is None
    assert personal_topic_owner("personal:abc") == "abc"
    assert personal_topic_owner("personal:") == ""
    assert is_personal_topic_forbidden("abc", "personal:abc") is False
    assert is_personal_topic_forbidden("abc", "personal:xyz") is True
    assert is_personal_topic_forbidden("abc", "personal:") is True
    assert is_personal_topic_forbidden("abc", "market:ore") is False


@pytest.mark.asyncio
async def test_owner_may_subscribe_personal_topic():
    manager = ConnectionManager()
    user_id = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, user_id, {"username": "owner"})

    topic = f"personal:{user_id}"
    assert manager.subscribe_topic(user_id, topic) is True
    assert user_id in manager.topic_subscriptions[topic]


@pytest.mark.asyncio
async def test_cross_user_personal_subscribe_denied_not_registered():
    manager = ConnectionManager()
    attacker = str(uuid4())
    victim = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, attacker, {"username": "attacker"})

    topic = f"personal:{victim}"
    assert manager.subscribe_topic(attacker, topic) is False
    assert attacker not in manager.topic_subscriptions.get(topic, set())


@pytest.mark.asyncio
async def test_malformed_personal_topic_denied():
    manager = ConnectionManager()
    user_id = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, user_id, {"username": "u"})

    assert manager.subscribe_topic(user_id, "personal:") is False
    assert "personal:" not in manager.topic_subscriptions


@pytest.mark.asyncio
async def test_handler_cross_user_rejects_and_logs_security_event():
    from src.services import websocket_service as ws_mod

    attacker = str(uuid4())
    victim = str(uuid4())
    ws = FakeWebSocket()
    await ws_mod.connection_manager.connect(ws, attacker, {"username": "attacker"})

    topic = f"personal:{victim}"
    with patch.object(ws_mod.logger, "warning") as warn:
        await handle_websocket_message(
            attacker,
            {"type": "subscribe_topic", "topic": topic},
        )

    frames = _frames(ws)
    assert any(
        f.get("type") == "subscription_rejected"
        and f.get("reason") == "personal topic ownership denied"
        and f.get("topic") == topic
        for f in frames
    )
    assert attacker not in ws_mod.connection_manager.topic_subscriptions.get(topic, set())
    assert warn.called
    assert "security_event personal_topic_cross_subscribe" in warn.call_args[0][0]


@pytest.mark.asyncio
async def test_handler_owner_subscribe_succeeds():
    from src.services import websocket_service as ws_mod

    user_id = str(uuid4())
    ws = FakeWebSocket()
    await ws_mod.connection_manager.connect(ws, user_id, {"username": "owner"})

    topic = f"personal:{user_id}"
    await handle_websocket_message(
        user_id,
        {"type": "subscribe_topic", "topic": topic},
    )

    frames = _frames(ws)
    assert any(f.get("type") == "topic_subscribed" and f.get("topic") == topic for f in frames)
    assert user_id in ws_mod.connection_manager.topic_subscriptions[topic]


@pytest.mark.asyncio
async def test_denied_attacker_does_not_receive_publish_topic():
    manager = ConnectionManager()
    attacker = str(uuid4())
    victim = str(uuid4())
    attacker_ws = FakeWebSocket()
    victim_ws = FakeWebSocket()
    await manager.connect(attacker_ws, attacker, {"username": "attacker"})
    await manager.connect(victim_ws, victim, {"username": "victim"})

    topic = f"personal:{victim}"
    assert manager.subscribe_topic(attacker, topic) is False
    assert manager.subscribe_topic(victim, topic) is True

    await manager.publish_topic(topic, {"type": "ping", "payload": "secret"})

    attacker_frames = _frames(attacker_ws)
    victim_frames = _frames(victim_ws)
    assert not any(f.get("payload") == "secret" for f in attacker_frames)
    assert any(f.get("payload") == "secret" for f in victim_frames)


@pytest.mark.asyncio
async def test_non_personal_and_cap_still_work():
    manager = ConnectionManager()
    user_id = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, user_id, {"username": "u"})

    assert manager.subscribe_topic(user_id, "market:ore") is True
    for i in range(MAX_TOPICS_PER_USER - 1):
        assert manager.subscribe_topic(user_id, f"topic-{i}") is True
    assert manager.subscribe_topic(user_id, "topic-overflow") is False
    assert manager.count_topic_subscriptions(user_id) == MAX_TOPICS_PER_USER
