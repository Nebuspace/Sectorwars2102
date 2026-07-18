"""Unit coverage for EnhancedWebSocketService.handle_message's fallback
dispatch to the base connection-message handler (WO-RT-MARKET-STREAM-CLIENT).

Bug pinned: ``self.connection_manager.handle_websocket_message(...)`` does
not exist -- ``handle_websocket_message`` is a MODULE-level function in
``websocket_service.py``, not a ``ConnectionManager`` method. Every message
type NOT explicitly routed by ``EnhancedWebSocketService.handle_message``
(``chat_message``, ``request_sector_players``, etc.) hit this dead fallback,
raised ``AttributeError``, and was silently swallowed by ``handle_message``'s
own except-clause -- surfacing to the player as a generic "Internal server
error" on every standard message sent over the enhanced trading socket.

Pure Python + the real (module-level) ``ConnectionManager`` singleton -- no
live DB, no live Redis, no live websocket.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Dict
from uuid import uuid4

import pytest

from src.api.routes.enhanced_websocket import _unwrap_pubsub_envelope
from src.services.enhanced_websocket_service import (
    EnhancedWebSocketService,
    WebSocketSession,
)


def _make_session(player_id: str) -> WebSocketSession:
    now = datetime.now(UTC)
    return WebSocketSession(
        session_id=str(uuid4()),
        player_id=player_id,
        connection_time=now,
        last_activity=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        authenticated=True,
    )


def _sign(service: EnhancedWebSocketService, message: Dict[str, Any]) -> str:
    content = json.dumps(
        {
            "type": message.get("type"),
            "timestamp": message.get("timestamp"),
            "session_id": message.get("session_id"),
        },
        sort_keys=True,
    )
    return hmac.new(
        service.message_secret.encode(), content.encode(), hashlib.sha256
    ).hexdigest()


def _recording_send_error(calls: list):
    """handle_message awaits send_error(...) -- the replacement must be a
    real coroutine function, not a plain lambda, or the except-clause's
    `await self.send_error(...)` raises its own TypeError."""

    async def _send_error(*args, **kwargs):
        calls.append((args, kwargs))

    return _send_error


def _standard_message(
    service: EnhancedWebSocketService,
    session: WebSocketSession,
    message_type: str,
    **extra: Any,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "type": message_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session.session_id,
        **extra,
    }
    message["signature"] = _sign(service, message)
    return message


@pytest.mark.unit
@pytest.mark.asyncio
class TestFallbackDispatch:
    """message_type values EnhancedWebSocketService.handle_message doesn't
    route itself (not market_subscribe/market_unsubscribe/trading_command/
    ai_request/aria_chat/automation_rule/heartbeat) must reach the base
    handler cleanly instead of raising AttributeError."""

    async def test_request_sector_players_reaches_base_handler_without_attributeerror(self, monkeypatch):
        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())
        session = _make_session(player_id)
        service.sessions[player_id] = session

        # handle_message's own except-clause SWALLOWS any exception raised
        # by the fallback branch and calls send_error instead of re-raising
        # -- "handle_message() didn't raise" is therefore true whether or
        # not the dispatch bug is present, and doesn't actually pin
        # anything on its own. Spy on send_error: it's the outward,
        # falsifiable signal that the except-clause fired.
        send_error_calls: list = []
        monkeypatch.setattr(service, "send_error", _recording_send_error(send_error_calls))

        message = _standard_message(service, session, "request_sector_players")
        await service.handle_message(player_id, message, db=None)

        assert send_error_calls == []

    async def test_chat_message_reaches_base_handler_without_attributeerror(self, monkeypatch):
        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())
        session = _make_session(player_id)
        service.sessions[player_id] = session

        send_error_calls: list = []
        monkeypatch.setattr(service, "send_error", _recording_send_error(send_error_calls))

        # Empty content short-circuits handle_websocket_message's chat
        # branch before it ever touches connection_metadata -- proves
        # dispatch succeeds without needing a live connection registered.
        message = _standard_message(service, session, "chat_message", content="")
        await service.handle_message(player_id, message, db=None)

        assert send_error_calls == []

    async def test_fallback_calls_the_module_level_function_directly(self, monkeypatch):
        """Pin the dead path precisely: patch
        websocket_service.handle_websocket_message and assert the enhanced
        service's fallback branch calls exactly THIS function (not some
        ConnectionManager method) with (player_id, message)."""
        import src.services.websocket_service as websocket_service_module

        calls = []

        async def fake_handle_websocket_message(user_id, message_data):
            calls.append((user_id, message_data))

        monkeypatch.setattr(
            websocket_service_module,
            "handle_websocket_message",
            fake_handle_websocket_message,
        )

        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())
        session = _make_session(player_id)
        service.sessions[player_id] = session

        message = _standard_message(service, session, "request_sector_players")
        await service.handle_message(player_id, message, db=None)

        assert len(calls) == 1
        called_player_id, called_message = calls[0]
        assert called_player_id == player_id
        assert called_message["type"] == "request_sector_players"

    async def test_specially_routed_types_never_reach_the_fallback(self, monkeypatch):
        """Regression guard: 'heartbeat' has its OWN branch in
        handle_message (_handle_heartbeat) -- it must never fall through to
        the base handler, so this pins that the two dispatch paths stay
        mutually exclusive."""
        import src.services.websocket_service as websocket_service_module

        calls = []

        async def fake_handle_websocket_message(user_id, message_data):
            calls.append((user_id, message_data))

        monkeypatch.setattr(
            websocket_service_module,
            "handle_websocket_message",
            fake_handle_websocket_message,
        )

        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())
        session = _make_session(player_id)
        service.sessions[player_id] = session

        message = _standard_message(service, session, "heartbeat")
        await service.handle_message(player_id, message, db=None)

        assert calls == []


@pytest.mark.unit
class TestMarketStreamEnvelopeUnwrap:
    """Bug pinned: RedisPubSubService.publish_market_update (see
    src/services/redis_pubsub_service.py:108-121) always wraps whatever
    ``market_data`` a caller gives it inside a ``{"type": "market_update",
    "commodity": ..., "data": ..., "timestamp": ...}`` envelope before
    publishing to Redis. public_market_stream's forwarding loop then wraps
    THAT payload again under its own top-level ``data`` key -- without
    unwrapping first, a client would need ``data.data.buy_price`` instead of
    ``data.buy_price``. _unwrap_pubsub_envelope is the fix: pure sync
    function, no event loop / real socket needed to pin it (WO-RT-MARKET-
    STREAM-CLIENT)."""

    def test_pre_enveloped_payload_forwards_single_wrapped(self):
        # Exact shape RedisPubSubService.publish_market_update produces.
        pubsub_envelope = {
            "type": "market_update",
            "commodity": "ore",
            "data": {"buy_price": 12.5, "sell_price": 11.0, "volume": 340},
            "timestamp": "2026-07-09T00:00:00+00:00",
        }

        unwrapped = _unwrap_pubsub_envelope(pubsub_envelope)

        assert unwrapped == {"buy_price": 12.5, "sell_price": 11.0, "volume": 340}
        # The forwarding loop puts this straight under its own "data" key --
        # confirm there is no leftover "data" key to double-wrap on.
        assert "data" not in unwrapped

    def test_non_enveloped_payload_passes_through_unchanged(self):
        # A future publisher that isn't pre-enveloped (no "type"/"data"
        # pair) must be forwarded byte-for-byte.
        raw_payload = {"buy_price": 9.0, "sell_price": 8.25}

        assert _unwrap_pubsub_envelope(raw_payload) == raw_payload

    def test_dict_with_data_key_but_no_type_key_passes_through_unchanged(self):
        # Guard the exact match condition: both "type" AND "data" must be
        # present, or this would over-eagerly unwrap unrelated payloads that
        # merely happen to have a "data" field.
        raw_payload = {"data": {"nested": True}, "commodity": "fuel"}

        assert _unwrap_pubsub_envelope(raw_payload) == raw_payload
