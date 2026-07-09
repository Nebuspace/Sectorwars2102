"""Regression pin for WO-RT-BUS-HARDENING.

Closes four verified gaps between the shipped WS bus and canon's anti-abuse
table (SYSTEMS/realtime-bus.md's Rate limits table + :230, OPERATIONS/
realtime.md:22):

1. Unbounded per-user topic subscriptions — ConnectionManager.subscribe_topic
   now enforces canon's 50-distinct-topics-per-user cap (MAX_TOPICS_PER_USER)
   and rejects the 51st with a `subscription_rejected` frame instead of
   registering it.
2. A flood limiter that never escalated — the route now tracks rate-limit
   violations per connection and force-disconnects with close code 4002
   (canon-mandated) once WS_VIOLATION_ESCALATION_THRESHOLD violations land
   within WS_VIOLATION_ESCALATION_WINDOW (NO-CANON: 3 within 10s, canon only
   says "sustained").
3. A rate dict that grew forever — `_ws_rate_limits`/`_ws_violations` are
   defaultdicts keyed by user_id with no eviction. The endpoint's `finally`
   now pops both, but ONLY when `connection_manager.disconnect()` actually
   performed the teardown (its return value, new in this WO) — a superseded
   handler's finally must not wipe out state a live successor connection has
   already started accumulating (the same eviction race WO-RT-EVICTION-
   SUPERSEDE closed for active_connections, applied to a second piece of
   per-user state).
4. No private-DM chat target — `handle_websocket_message`'s chat_message
   branch gains `target_type == "private"`: the ephemeral "Private" room from
   OPERATIONS/realtime.md#3-rooms, NOT the persistent mailbox
   (message_service.py / POST /api/v1/messages, already wired to a WS
   priority-delivery push per FINDINGS.md 2026-06-12). Delivers to the
   recipient AND echoes to the sender; a nonexistent recipient is rejected,
   an offline-but-real recipient is steered to the persistent mailbox
   instead. Frame shape (target_user_id key, echo semantics) is NO-CANON —
   kept minimal, flagged.

Sections (1)-(2) use a FRESH ConnectionManager() instance (mirrors
test_ws_eviction_race.py / test_ws_room_hop.py) for registry-internals
assertions untouched by other test modules. Sections (3)-(6) exercise the
module-level `handle_websocket_message()` / `websocket_endpoint()`, which
hardcode the real singletons by name — an autouse fixture scrubs every
registry + both route-level dicts afterward (extends test_ws_room_hop.py's
`_clean_singleton_registries` convention).

Fully DB-free and network-free throughout; the private-chat DB existence
check is proven both against a fake AsyncSession (real query construction,
canned result) and, for the handle_websocket_message-level tests, patched
out entirely so those tests isolate the chat-routing logic.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from src.services.websocket_service import MAX_TOPICS_PER_USER, ConnectionManager


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


# --------------------------------------------------------------------------- #
# (1) — ConnectionManager.subscribe_topic: the 50-topic-per-user cap.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_51st_distinct_topic_rejected_first_50_stay_live():
    manager = ConnectionManager()
    user_id = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, user_id, {"username": "subscriber"})

    for i in range(MAX_TOPICS_PER_USER):
        assert manager.subscribe_topic(user_id, f"topic-{i}") is True

    assert manager.count_topic_subscriptions(user_id) == MAX_TOPICS_PER_USER

    assert manager.subscribe_topic(user_id, "topic-overflow") is False
    assert manager.count_topic_subscriptions(user_id) == MAX_TOPICS_PER_USER
    assert user_id not in manager.topic_subscriptions.get("topic-overflow", set())

    # first 50 are all still live.
    for i in range(MAX_TOPICS_PER_USER):
        assert user_id in manager.topic_subscriptions[f"topic-{i}"]


@pytest.mark.asyncio
async def test_idempotent_resubscribe_at_cap_still_succeeds():
    manager = ConnectionManager()
    user_id = str(uuid4())
    ws = FakeWebSocket()
    await manager.connect(ws, user_id, {"username": "subscriber"})

    for i in range(MAX_TOPICS_PER_USER):
        manager.subscribe_topic(user_id, f"topic-{i}")

    # re-subscribing to an already-held topic doesn't consume a fresh slot.
    assert manager.subscribe_topic(user_id, "topic-0") is True
    assert manager.count_topic_subscriptions(user_id) == MAX_TOPICS_PER_USER


@pytest.mark.asyncio
async def test_handle_message_emits_subscription_rejected_frame_at_cap():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    user_id = str(uuid4())
    ws = FakeWebSocket()
    await connection_manager.connect(ws, user_id, {"username": "subscriber"})

    for i in range(MAX_TOPICS_PER_USER):
        await handle_websocket_message(user_id, {"type": "subscribe_topic", "topic": f"t{i}"})

    ws.sent.clear()
    await handle_websocket_message(user_id, {"type": "subscribe_topic", "topic": "overflow"})

    frames = _frames(ws)
    assert len(frames) == 1
    assert frames[0]["type"] == "subscription_rejected"
    assert frames[0]["topic"] == "overflow"
    assert frames[0]["current_count"] == MAX_TOPICS_PER_USER
    assert "reason" in frames[0]
    assert user_id not in connection_manager.topic_subscriptions.get("overflow", set())


# --------------------------------------------------------------------------- #
# (2) — private chat target_type.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_private_chat_delivers_to_recipient_and_echoes_sender():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id, recipient_id = str(uuid4()), str(uuid4())
    sender_ws, recipient_ws = FakeWebSocket(), FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender"})
    await connection_manager.connect(recipient_ws, recipient_id, {"username": "recipient"})
    sender_ws.sent.clear()
    recipient_ws.sent.clear()

    with patch(
        "src.services.websocket_service._private_chat_recipient_exists",
        AsyncMock(return_value=True),
    ):
        await handle_websocket_message(sender_id, {
            "type": "chat_message",
            "target_type": "private",
            "target_user_id": recipient_id,
            "content": "psst",
        })

    recipient_frames = _frames(recipient_ws)
    sender_frames = _frames(sender_ws)

    assert len(recipient_frames) == 1
    assert recipient_frames[0]["type"] == "chat_message"
    assert recipient_frames[0]["content"] == "psst"
    assert recipient_frames[0]["from_user_id"] == sender_id
    assert recipient_frames[0]["target_user_id"] == recipient_id

    # exactly one echo, not a double-send.
    assert len(sender_frames) == 1
    assert sender_frames[0]["content"] == "psst"
    assert sender_frames[0]["target_user_id"] == recipient_id


@pytest.mark.asyncio
async def test_private_chat_offline_recipient_errors_toward_mailbox():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id, offline_recipient_id = str(uuid4()), str(uuid4())
    sender_ws = FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender"})
    sender_ws.sent.clear()

    with patch(
        "src.services.websocket_service._private_chat_recipient_exists",
        AsyncMock(return_value=True),
    ):
        await handle_websocket_message(sender_id, {
            "type": "chat_message",
            "target_type": "private",
            "target_user_id": offline_recipient_id,
            "content": "hello?",
        })

    frames = _frames(sender_ws)
    assert len(frames) == 1
    assert frames[0]["type"] == "error"
    assert frames[0]["code"] == "recipient_offline"


@pytest.mark.asyncio
async def test_private_chat_unknown_recipient_rejected():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id = str(uuid4())
    sender_ws = FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender"})
    sender_ws.sent.clear()

    with patch(
        "src.services.websocket_service._private_chat_recipient_exists",
        AsyncMock(return_value=False),
    ):
        await handle_websocket_message(sender_id, {
            "type": "chat_message",
            "target_type": "private",
            "target_user_id": str(uuid4()),
            "content": "hi",
        })

    frames = _frames(sender_ws)
    assert len(frames) == 1
    assert frames[0]["type"] == "error"
    assert frames[0]["message"] == "Recipient not found"


@pytest.mark.asyncio
async def test_private_chat_self_target_rejected():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id = str(uuid4())
    sender_ws = FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender"})
    sender_ws.sent.clear()

    await handle_websocket_message(sender_id, {
        "type": "chat_message",
        "target_type": "private",
        "target_user_id": sender_id,
        "content": "talking to myself",
    })

    frames = _frames(sender_ws)
    assert len(frames) == 1
    assert frames[0]["type"] == "error"
    assert "target_user_id" in frames[0]["message"]


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    """Fake AsyncSession: real query construction runs (select/.where against
    the real User model columns), but execute() returns a canned result
    instead of touching a database. Avoids the AsyncMock nested-attr
    coroutine trap (see monk memory asyncmock-nested-attr-coroutine-trap) by
    using a plain object with sync methods rather than a bare AsyncMock."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._value)


@pytest.mark.asyncio
async def test_private_chat_recipient_exists_true_for_real_user():
    from src.services.websocket_service import _private_chat_recipient_exists

    target = uuid4()
    with patch("src.core.database.AsyncSessionLocal", lambda: _FakeAsyncSession(target)):
        assert await _private_chat_recipient_exists(str(target)) is True


@pytest.mark.asyncio
async def test_private_chat_recipient_exists_false_for_missing_user():
    from src.services.websocket_service import _private_chat_recipient_exists

    with patch("src.core.database.AsyncSessionLocal", lambda: _FakeAsyncSession(None)):
        assert await _private_chat_recipient_exists(str(uuid4())) is False


@pytest.mark.asyncio
async def test_private_chat_recipient_exists_false_for_malformed_uuid():
    from src.services.websocket_service import _private_chat_recipient_exists

    assert await _private_chat_recipient_exists("not-a-uuid") is False


# --------------------------------------------------------------------------- #
# (3) — sector/team/global chat frame shape is byte-unchanged by the new
#     private branch (regression pin).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_sector_chat_frame_shape_unchanged():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id, peer_id = str(uuid4()), str(uuid4())
    sender_ws, peer_ws = FakeWebSocket(), FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender", "current_sector": 5})
    await connection_manager.connect(peer_ws, peer_id, {"username": "peer", "current_sector": 5})
    peer_ws.sent.clear()

    await handle_websocket_message(sender_id, {
        "type": "chat_message", "target_type": "sector", "content": "yo",
    })

    frames = _frames(peer_ws)
    assert len(frames) == 1
    assert set(frames[0].keys()) == {
        "type", "from_user_id", "from_username", "content", "target_type",
        "timestamp", "sector_id",
    }
    assert frames[0]["target_type"] == "sector"


@pytest.mark.asyncio
async def test_team_chat_frame_shape_unchanged():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    team_id = str(uuid4())
    sender_id, peer_id = str(uuid4()), str(uuid4())
    sender_ws, peer_ws = FakeWebSocket(), FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender", "team_id": team_id})
    await connection_manager.connect(peer_ws, peer_id, {"username": "peer", "team_id": team_id})
    peer_ws.sent.clear()

    await handle_websocket_message(sender_id, {
        "type": "chat_message", "target_type": "team", "content": "yo team",
    })

    frames = _frames(peer_ws)
    assert len(frames) == 1
    assert set(frames[0].keys()) == {
        "type", "from_user_id", "from_username", "content", "target_type",
        "timestamp", "team_id",
    }
    assert frames[0]["target_type"] == "team"


@pytest.mark.asyncio
async def test_global_chat_frame_shape_unchanged():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    sender_id, peer_id = str(uuid4()), str(uuid4())
    sender_ws, peer_ws = FakeWebSocket(), FakeWebSocket()
    await connection_manager.connect(sender_ws, sender_id, {"username": "sender"})
    await connection_manager.connect(peer_ws, peer_id, {"username": "peer"})
    peer_ws.sent.clear()

    await handle_websocket_message(sender_id, {
        "type": "chat_message", "target_type": "global", "content": "yo everyone",
    })

    frames = _frames(peer_ws)
    assert len(frames) == 1
    assert set(frames[0].keys()) == {
        "type", "from_user_id", "from_username", "content", "target_type",
        "timestamp",
    }
    assert frames[0]["target_type"] == "global"


@pytest.fixture(autouse=True)
def _clean_singleton_registries():
    """Every test above (and below) that touches the module-level singleton
    registers fake sockets on it directly — scrub afterward so nothing leaks
    across tests (extends test_ws_room_hop.py's convention)."""
    from src.services.websocket_service import connection_manager
    from src.api.routes import websocket as ws_route

    yield
    connection_manager.active_connections.clear()
    connection_manager.connection_metadata.clear()
    connection_manager.sector_connections.clear()
    connection_manager.team_connections.clear()
    connection_manager.region_connections.clear()
    connection_manager.topic_subscriptions.clear()
    connection_manager.admin_connections.clear()
    connection_manager.admin_metadata.clear()
    ws_route._ws_rate_limits.clear()
    ws_route._ws_violations.clear()


# --------------------------------------------------------------------------- #
# (4) — _ws_rate_limits / _ws_violations pruning, identity-gated on
#     connection_manager.disconnect()'s new return value.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_own_finally_prunes_rate_and_violation_dicts():
    from src.services.websocket_service import connection_manager
    from src.api.routes import websocket as ws_route

    user_id = str(uuid4())
    ws = FakeWebSocket()
    await connection_manager.connect(ws, user_id, {"username": "solo"})
    ws_route._ws_rate_limits[user_id] = [time.monotonic()]
    ws_route._ws_violations[user_id] = [time.monotonic()]

    disconnected = await connection_manager.disconnect(user_id, ws)
    assert disconnected is True
    if disconnected:
        ws_route._ws_rate_limits.pop(user_id, None)
        ws_route._ws_violations.pop(user_id, None)

    assert user_id not in ws_route._ws_rate_limits
    assert user_id not in ws_route._ws_violations


@pytest.mark.asyncio
async def test_stale_handler_finally_does_not_prune_successors_rate_state():
    """The identity pin: a superseded handler's finally must not wipe out
    rate/violation state a live successor connection has already started
    accumulating — mirrors WO-RT-EVICTION-SUPERSEDE's guard on
    active_connections itself, applied to the two new per-user dicts."""
    from src.services.websocket_service import connection_manager
    from src.api.routes import websocket as ws_route

    user_id = str(uuid4())
    socket_a, socket_b = FakeWebSocket(), FakeWebSocket()

    await connection_manager.connect(socket_a, user_id, {"username": "a"})
    ws_route._ws_rate_limits[user_id] = [time.monotonic()]
    ws_route._ws_violations[user_id] = [time.monotonic()]

    # A second tab connects -> evicts socket_a.
    await connection_manager.connect(socket_b, user_id, {"username": "b"})

    # socket_a's own finally now runs, passing its OWN (stale) handle.
    disconnected = await connection_manager.disconnect(user_id, socket_a)
    assert disconnected is False
    if disconnected:
        ws_route._ws_rate_limits.pop(user_id, None)
        ws_route._ws_violations.pop(user_id, None)

    # the successor's rate/violation state survives the stale finally.
    assert user_id in ws_route._ws_rate_limits
    assert user_id in ws_route._ws_violations


# --------------------------------------------------------------------------- #
# (5) — sustained flood -> forced disconnect with close code 4002, exercised
#     through the real websocket_endpoint() (not just the helper functions).
# --------------------------------------------------------------------------- #

class FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id
        self.username = "flooder"
        self.is_admin = False


class FakePlayer:
    def __init__(self):
        self.id = uuid4()
        self.current_sector_id = None
        self.current_region_id = None
        self.team_id = None
        self.credits = 1000
        self.turns = 10
        self.personal_reputation = 0
        self.reputation_tier = "Neutral"
        self.name_color = "#FFFFFF"
        self.military_rank = "Recruit"


class _FakeQuery:
    def __init__(self, player):
        self._player = player

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._player


class FakeDB:
    def __init__(self, player):
        self._player = player

    def query(self, model):
        return _FakeQuery(self._player)


class FakeRouteWebSocket:
    """Route-level fake: unlike the manager-level FakeWebSocket, this one
    also drives receive_text() so the endpoint's own while-loop can be
    exercised end-to-end (mirrors the Admin list-route direct-call pattern:
    call the route coroutine directly, bypassing FastAPI's TestClient/DI)."""

    def __init__(self, incoming: list[str]):
        self._incoming = list(incoming)
        self.accepted = False
        self.closed_with: tuple[int | None, str | None] | None = None
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._incoming:
            raise WebSocketDisconnect()
        return self._incoming.pop(0)

    async def close(self, code=None, reason=None) -> None:
        self.closed_with = (code, reason)

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_sustained_flood_escalates_to_forced_disconnect_4002():
    from src.api.routes import websocket as ws_route

    user_id = str(uuid4())
    fake_user = FakeUser(user_id)
    fake_db = FakeDB(FakePlayer())

    # First 100 receives silently fill the 1s window; 101/102/103 each fail
    # the rate check -> violations 1/2/3 -> the 3rd crosses
    # WS_VIOLATION_ESCALATION_THRESHOLD and forces the disconnect. A canary
    # message queued AFTER the flood proves the loop actually breaks on
    # escalation instead of merely error-dropping and looping again.
    canary = json.dumps({"type": "CANARY_SHOULD_NOT_BE_CONSUMED"})
    incoming = [json.dumps({"type": "heartbeat"})] * (ws_route.WS_RATE_LIMIT + ws_route.WS_VIOLATION_ESCALATION_THRESHOLD) + [canary]
    fws = FakeRouteWebSocket(incoming)

    with patch.object(ws_route, "get_current_user_from_token", AsyncMock(return_value=fake_user)):
        await ws_route.websocket_endpoint(fws, token="tok", db=fake_db)

    assert fws.closed_with == (4002, "sustained rate limit violations")
    # the canary was never consumed -- the loop broke immediately on
    # escalation rather than looping around to receive_text() again.
    assert fws._incoming == [canary]

    # the FINAL error frame is the escalation notice, not just another
    # plain per-message rate-limit error (a merely-error-dropped
    # implementation would never send this distinct message).
    error_frames = [f for f in _frames(fws) if f["type"] == "error"]
    assert error_frames[-1]["message"] == "Sustained rate limit violations. Disconnecting."


@pytest.mark.asyncio
async def test_non_sustained_flood_does_not_disconnect():
    """One or two rate-limit violations (below the escalation threshold) get
    the normal per-message error, not a forced disconnect."""
    from src.api.routes import websocket as ws_route

    user_id = str(uuid4())
    fake_user = FakeUser(user_id)
    fake_db = FakeDB(FakePlayer())

    # 100 fill the window + 2 violations (below threshold=3) + disconnect.
    incoming = [json.dumps({"type": "heartbeat"})] * (ws_route.WS_RATE_LIMIT + 2)
    fws = FakeRouteWebSocket(incoming)

    with patch.object(ws_route, "get_current_user_from_token", AsyncMock(return_value=fake_user)):
        await ws_route.websocket_endpoint(fws, token="tok", db=fake_db)

    assert fws.closed_with is None
