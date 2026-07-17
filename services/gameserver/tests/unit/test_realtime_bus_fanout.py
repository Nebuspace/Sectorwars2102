"""Regression + round-trip coverage for WO-P1-REALTIME-BUS-FANOUT.

Before this WO, ConnectionManager.send_personal_message / broadcast_to_sector
(websocket_service.py) only ever iterated this worker's own in-process
active_connections/sector_connections -- a user connected to a DIFFERENT
uvicorn worker (or a sector with members split across workers) never saw the
event. Only market/trading/ai channels fanned out cross-process
(redis_pubsub_service.py's publish_market_update/publish_trading_event/
publish_ai_signal); personal/sector delivery was purely local.

Fixed by:
  - redis_pubsub_service.RedisPubSubService gains a generic BUS_CHANNEL
    ("sw2102:bus") with publish_bus_event() / subscribe_bus() -- a single
    per-process listener, distinct from the per-commodity market
    subscriptions, tagged with a per-instance worker_id.
  - websocket_service.ConnectionManager splits send_personal_message /
    broadcast_to_sector into a local-delivery half (_deliver_personal_local /
    _deliver_sector_local, byte-identical to the pre-WO bodies) and a
    bus-publish half (_publish_to_bus). send_personal_message publishes only
    on a local miss (the single-socket-per-user invariant means a hit here
    means the user isn't connected anywhere else); broadcast_to_sector always
    publishes (a sector's membership is inherently split across workers).
  - connect() lazily starts a once-per-process bus-subscriber task
    (_ensure_bus_subscriber_started) that dispatches inbound envelopes via
    _handle_bus_envelope, which skips envelopes carrying this worker's OWN
    origin_worker_id (already delivered locally, synchronously, before the
    publish call -- the duplicate-delivery guard) and otherwise calls the
    local-delivery-only methods (never re-publishes -- that would ping-pong
    the same event across every worker forever).

No local Redis exists on this Mac, and fakeredis is NOT in this repo's
dependency tree (probed live: `import fakeredis` raises ModuleNotFoundError;
per the standing no-new-deps gate, not added). The classes below are a
minimal, faithful in-memory pub/sub broker built for this test only --
NOT a general-purpose fakeredis replacement. Fidelity limits: single
process/event-loop only (no real network, no cross-process semantics to
fake); no PATTERN subscriptions (PSUBSCRIBE), no consumer groups, no
persistence/replay of messages published before a subscriber joins (real
Redis pub/sub doesn't replay either, so this matches); message ordering
is FIFO per-channel via asyncio.Queue, matching real single-channel
ordering. Two independent RedisPubSubService instances pointed at the
SAME _FakeBusBroker simulate two uvicorn workers talking through one real
Redis instance.

Fully DB-free and network-free throughout.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.redis_pubsub_service import RedisPubSubService
from src.services.websocket_service import ConnectionManager


# --------------------------------------------------------------------------- #
# Minimal in-memory Redis pub/sub fake (see module docstring for fidelity
# limits). Mirrors just the surface RedisPubSubService actually calls:
# redis_client.publish(channel, data) / redis_client.pubsub() -> an object
# with async subscribe(*channels) / listen() / unsubscribe() / close().
# --------------------------------------------------------------------------- #

class _FakeBusBroker:
    """Channel -> list of subscriber queues, shared across every
    _FakeRedisClient pointed at it."""

    def __init__(self) -> None:
        self._channel_queues: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, channel: str, queue: "asyncio.Queue") -> None:
        self._channel_queues.setdefault(channel, []).append(queue)

    def unsubscribe(self, channel: str, queue: "asyncio.Queue") -> None:
        queues = self._channel_queues.get(channel, [])
        if queue in queues:
            queues.remove(queue)

    async def publish(self, channel: str, data: str) -> int:
        queues = self._channel_queues.get(channel, [])
        for q in queues:
            await q.put(data)
        return len(queues)


class _FakePubSub:
    """Mirrors redis.asyncio.client.PubSub's listen() shape: the first
    yielded frame is a {"type": "subscribe", ...} confirmation (real
    redis-py behavior), real published payloads arrive as
    {"type": "message", "data": ...} -- exactly what subscribe_bus /
    subscribe_to_market_updates already filter on."""

    def __init__(self, broker: _FakeBusBroker) -> None:
        self._broker = broker
        self._queue: asyncio.Queue = asyncio.Queue()
        self._channels: list[str] = []

    async def subscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.append(channel)
            self._broker.subscribe(channel, self._queue)
            await self._queue.put(("__subscribe_ack__", channel))

    async def listen(self):
        while True:
            item = await self._queue.get()
            if isinstance(item, tuple) and item[0] == "__subscribe_ack__":
                yield {"type": "subscribe", "channel": item[1], "data": 1}
                continue
            yield {"type": "message", "channel": None, "data": item}

    async def unsubscribe(self) -> None:
        for channel in self._channels:
            self._broker.unsubscribe(channel, self._queue)

    async def close(self) -> None:
        pass


class _FakeRedisClient:
    def __init__(self, broker: _FakeBusBroker) -> None:
        self._broker = broker

    async def publish(self, channel: str, data: str) -> int:
        return await self._broker.publish(channel, data)

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._broker)

    async def ping(self) -> bool:
        return True


def _pubsub_service(broker: _FakeBusBroker) -> RedisPubSubService:
    service = RedisPubSubService()
    service.redis_client = _FakeRedisClient(broker)
    return service


class FakeWebSocket:
    """Minimal WebSocket stand-in: records send_text() calls (mirrors
    test_ws_room_hop.py / test_ws_eviction_race.py's own FakeWebSocket)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def close(self, code=None, reason=None) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


async def _drain(n: int = 5) -> None:
    """Yield control back to the event loop N times so a background
    subscriber task's queue.get()/callback chain gets to run before the
    test asserts on its side effects."""
    for _ in range(n):
        await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# (1) RedisPubSubService.publish_bus_event / subscribe_bus, in isolation --
#     the fake-broker round trip at the lowest level, no ConnectionManager.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
@pytest.mark.asyncio
class TestBusChannelPublishSubscribe:
    async def test_published_envelope_reaches_a_subscriber(self):
        broker = _FakeBusBroker()
        publisher = _pubsub_service(broker)
        listener = _pubsub_service(broker)

        received: list[dict] = []

        async def callback(envelope):
            received.append(envelope)

        task = asyncio.create_task(listener.subscribe_bus(callback))
        await _drain()

        await publisher.publish_bus_event("personal", "bob", {"type": "hi"})
        await _drain()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(received) == 1
        assert received[0]["kind"] == "personal"
        assert received[0]["target"] == "bob"
        assert received[0]["message"] == {"type": "hi"}
        assert received[0]["origin_worker_id"] == publisher.worker_id

    async def test_malformed_json_is_logged_and_does_not_kill_the_loop(self):
        broker = _FakeBusBroker()
        listener = _pubsub_service(broker)
        publisher_client = _FakeRedisClient(broker)

        received: list[dict] = []

        async def callback(envelope):
            received.append(envelope)

        task = asyncio.create_task(listener.subscribe_bus(callback))
        await _drain()

        # A malformed frame lands on the channel directly (bypassing
        # publish_bus_event's own json.dumps, simulating real-world channel
        # corruption/version-skew) -- the loop must survive it...
        await publisher_client.publish(listener.BUS_CHANNEL, "{not valid json")
        await _drain()
        # ...and still deliver the NEXT, well-formed message.
        await publisher_client.publish(
            listener.BUS_CHANNEL,
            json.dumps({"kind": "sector", "target": 5, "message": {"type": "ok"},
                        "origin_worker_id": "someone-else"}),
        )
        await _drain()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(received) == 1
        assert received[0]["message"] == {"type": "ok"}

    async def test_callback_exception_does_not_kill_the_loop(self):
        broker = _FakeBusBroker()
        listener = _pubsub_service(broker)
        publisher = _pubsub_service(broker)

        calls = {"n": 0}

        async def flaky_callback(envelope):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")

        task = asyncio.create_task(listener.subscribe_bus(flaky_callback))
        await _drain()

        await publisher.publish_bus_event("personal", "bob", {"type": "first"})
        await _drain()
        await publisher.publish_bus_event("personal", "bob", {"type": "second"})
        await _drain()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# (2) ConnectionManager local-delivery extraction is behavior-preserving,
#     and _handle_bus_envelope's dedup + routing, in isolation (mocked
#     get_pubsub_service -- no real bus involved).
# --------------------------------------------------------------------------- #

@pytest.mark.unit
@pytest.mark.asyncio
class TestHandleBusEnvelope:
    async def test_same_origin_envelope_is_skipped(self):
        manager = ConnectionManager()
        user_id = str(uuid4())
        ws = FakeWebSocket()
        manager.active_connections[user_id] = ws

        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            await manager._handle_bus_envelope({
                "kind": "personal",
                "target": user_id,
                "message": {"type": "should_not_arrive"},
                "origin_worker_id": "worker-self",
            })

        assert ws.sent == []

    async def test_different_origin_personal_envelope_delivers_locally(self):
        manager = ConnectionManager()
        user_id = str(uuid4())
        ws = FakeWebSocket()
        manager.active_connections[user_id] = ws

        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            await manager._handle_bus_envelope({
                "kind": "personal",
                "target": user_id,
                "message": {"type": "remote_event"},
                "origin_worker_id": "worker-other",
            })

        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["type"] == "remote_event"

    async def test_different_origin_sector_envelope_delivers_locally(self):
        manager = ConnectionManager()
        user_id = str(uuid4())
        ws = FakeWebSocket()
        manager.active_connections[user_id] = ws
        manager.sector_connections[42] = {user_id}

        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            await manager._handle_bus_envelope({
                "kind": "sector",
                "target": 42,
                "message": {"type": "remote_sector_event"},
                "origin_worker_id": "worker-other",
                "exclude_user": None,
            })

        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["type"] == "remote_sector_event"

    async def test_sector_envelope_honors_exclude_user(self):
        manager = ConnectionManager()
        excluded_id, other_id = str(uuid4()), str(uuid4())
        excluded_ws, other_ws = FakeWebSocket(), FakeWebSocket()
        manager.active_connections[excluded_id] = excluded_ws
        manager.active_connections[other_id] = other_ws
        manager.sector_connections[7] = {excluded_id, other_id}

        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            await manager._handle_bus_envelope({
                "kind": "sector",
                "target": 7,
                "message": {"type": "remote_sector_event"},
                "origin_worker_id": "worker-other",
                "exclude_user": excluded_id,
            })

        assert excluded_ws.sent == []
        assert len(other_ws.sent) == 1

    async def test_malformed_message_payload_is_dropped_not_raised(self):
        manager = ConnectionManager()
        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            # message is a string, not a dict -- must log and return, not raise
            await manager._handle_bus_envelope({
                "kind": "personal",
                "target": "bob",
                "message": "not-a-dict",
                "origin_worker_id": "worker-other",
            })

    async def test_unknown_kind_is_dropped_not_raised(self):
        manager = ConnectionManager()
        fake_pubsub = AsyncMock()
        fake_pubsub.worker_id = "worker-self"

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=fake_pubsub),
        ):
            await manager._handle_bus_envelope({
                "kind": "carrier_pigeon",
                "target": "bob",
                "message": {"type": "x"},
                "origin_worker_id": "worker-other",
            })


# --------------------------------------------------------------------------- #
# (3) End-to-end: two independent ConnectionManager + RedisPubSubService
#     pairs sharing one fake broker -- the literal WO proof ("publish
#     personal + sector events through the bus and assert a SECOND
#     connection-manager instance receives them").
# --------------------------------------------------------------------------- #

@pytest.mark.unit
@pytest.mark.asyncio
class TestCrossWorkerRoundTrip:
    async def test_personal_message_crosses_to_a_second_connection_manager(self):
        """cm_a doesn't have bob locally (simulating a different worker) --
        send_personal_message must fall through to the bus, and cm_b's live
        subscriber (bound to pubsub_b) must deliver it to bob's local
        socket."""
        broker = _FakeBusBroker()
        pubsub_b = _pubsub_service(broker)

        cm_a = ConnectionManager()
        cm_b = ConnectionManager()
        bob_ws = FakeWebSocket()
        cm_b.active_connections["bob"] = bob_ws

        subscriber_task = asyncio.create_task(
            pubsub_b.subscribe_bus(cm_b._handle_bus_envelope)
        )
        await _drain()

        # cm_a publishes through its OWN distinct pubsub instance (pubsub_a,
        # not pubsub_b) so origin_worker_id genuinely differs from the
        # listening side -- get_pubsub_service is a process-wide singleton
        # getter in production, so each side's call is patched separately
        # to simulate two different workers' independent getter resolution.
        pubsub_a = _pubsub_service(broker)
        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_a),
        ):
            delivered_locally = await cm_a.send_personal_message("bob", {"type": "trade_confirm"})

        # The publish (above) completes synchronously under pubsub_a's
        # patch, but cm_b's subscriber callback (_handle_bus_envelope) runs
        # ASYNCHRONOUSLY on the background task -- it needs get_pubsub_
        # service patched to pubsub_b (cm_b/pubsub_b's own identity, for the
        # origin_worker_id dedup check) at the moment IT actually executes,
        # which is during this drain, not during the publish above.
        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_b),
        ):
            await _drain()

        subscriber_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscriber_task

        assert delivered_locally is False  # cm_a never had bob locally
        assert len(bob_ws.sent) == 1
        assert json.loads(bob_ws.sent[0])["type"] == "trade_confirm"

    async def test_sector_broadcast_crosses_to_a_second_connection_manager(self):
        broker = _FakeBusBroker()
        pubsub_a = _pubsub_service(broker)
        pubsub_b = _pubsub_service(broker)

        cm_a = ConnectionManager()
        cm_b = ConnectionManager()
        remote_member_ws = FakeWebSocket()
        cm_b.active_connections["remote-member"] = remote_member_ws
        cm_b.sector_connections[99] = {"remote-member"}

        subscriber_task = asyncio.create_task(
            pubsub_b.subscribe_bus(cm_b._handle_bus_envelope)
        )
        await _drain()

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_a),
        ):
            # cm_a has zero local members of sector 99 -- broadcast_to_sector
            # must still publish (unconditional, unlike send_personal_message).
            await cm_a.broadcast_to_sector(99, {"type": "ship_arrived"})

        # cm_b's subscriber callback fires asynchronously during this drain
        # and needs get_pubsub_service patched to pubsub_b (its own
        # identity) at that moment -- same reasoning as the personal-
        # message round trip above.
        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_b),
        ):
            await _drain()

        subscriber_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscriber_task

        assert len(remote_member_ws.sent) == 1
        assert json.loads(remote_member_ws.sent[0])["type"] == "ship_arrived"

    async def test_sector_broadcast_does_not_double_deliver_to_its_own_origin_worker(self):
        """The duplicate-delivery guard: cm_b broadcasts to a sector it HAS
        local members in. Local delivery happens once (synchronously,
        inside broadcast_to_sector), and the bus-publish echoes back to
        cm_b's OWN live subscriber -- which must skip it (same
        origin_worker_id), not deliver a second copy."""
        broker = _FakeBusBroker()
        pubsub_b = _pubsub_service(broker)

        cm_b = ConnectionManager()
        local_member_ws = FakeWebSocket()
        cm_b.active_connections["local-member"] = local_member_ws
        cm_b.sector_connections[11] = {"local-member"}

        subscriber_task = asyncio.create_task(
            pubsub_b.subscribe_bus(cm_b._handle_bus_envelope)
        )
        await _drain()

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_b),
        ):
            # Patch must stay active through the drain, not just the
            # publish call: the echoed message's dedup check runs inside
            # cm_b._handle_bus_envelope, which fires asynchronously on the
            # subscriber task and needs get_pubsub_service to resolve to
            # pubsub_b (its own identity) at THAT moment to correctly
            # match origin_worker_id.
            await cm_b.broadcast_to_sector(11, {"type": "combat_started"})
            await _drain(10)

        subscriber_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscriber_task

        assert len(local_member_ws.sent) == 1
        assert json.loads(local_member_ws.sent[0])["type"] == "combat_started"

    async def test_repeat_publish_does_not_accumulate_duplicate_deliveries(self):
        """Sanity extension of the dedup proof across multiple events in
        sequence -- not just a single lucky pass."""
        broker = _FakeBusBroker()
        pubsub_b = _pubsub_service(broker)

        cm_b = ConnectionManager()
        member_ws = FakeWebSocket()
        cm_b.active_connections["member"] = member_ws
        cm_b.sector_connections[3] = {"member"}

        subscriber_task = asyncio.create_task(
            pubsub_b.subscribe_bus(cm_b._handle_bus_envelope)
        )
        await _drain()

        with patch(
            "src.services.redis_pubsub_service.get_pubsub_service",
            AsyncMock(return_value=pubsub_b),
        ):
            for i in range(3):
                await cm_b.broadcast_to_sector(3, {"type": "tick", "n": i})
                await _drain()

        subscriber_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await subscriber_task

        assert len(member_ws.sent) == 3
        assert [json.loads(f)["n"] for f in member_ws.sent] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# (4) grep-proof that market/trading are no longer the only channels
#     (WO's own stated proof requirement).
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestBusChannelIsGeneric:
    def test_bus_channel_constant_exists_and_differs_from_market_trading(self):
        service = RedisPubSubService()
        assert service.BUS_CHANNEL == "sw2102:bus"
        assert not service.BUS_CHANNEL.startswith(service.MARKET_CHANNEL_PREFIX)
        assert not service.BUS_CHANNEL.startswith(service.TRADING_CHANNEL_PREFIX)

    def test_two_instances_get_distinct_worker_ids(self):
        a, b = RedisPubSubService(), RedisPubSubService()
        assert a.worker_id != b.worker_id
