"""LEG-3673 — message send-path honest error surfacing densify.

Pins MessageService.send_message and POST /messages/send so downstream live-
notification / offline-push transport gaps return structured honest payloads
instead of opaque HTTP 500s with raw exception text.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import messages as messages_mod
from src.api.routes.messages import MessageCreateRequest, send_message
from src.services.message_service import MessageService
from src.services.notification_service import (
    MessageDeliveryError,
    NotificationDispatchResult,
)


def _first_mock(value):
    q = MagicMock()
    q.filter.return_value.first.return_value = value
    return q


def make_db(*query_results):
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


@pytest.mark.asyncio
async def test_send_message_raises_structured_delivery_error_when_ws_fails():
    sender_id, recipient_id = uuid.uuid4(), uuid.uuid4()
    sender = SimpleNamespace(id=sender_id, nickname="Ava", user=None)
    recipient = SimpleNamespace(id=recipient_id)

    db = make_db(
        _first_mock(sender),
        _first_mock(recipient),
    )
    message_id = uuid.uuid4()

    def _capture_add(msg):
        msg.id = message_id

    db.add.side_effect = _capture_add

    failed_dispatch = NotificationDispatchResult(
        error_code="live_notification_failed",
        error_message="Message was saved to inbox but the live notification could not be delivered.",
    )

    with patch.object(
        MessageService,
        "_send_notification",
        new=AsyncMock(return_value=failed_dispatch),
    ):
        with pytest.raises(MessageDeliveryError) as excinfo:
            await MessageService.send_message(
                db,
                sender_id=sender_id,
                recipient_id=recipient_id,
                content="ping",
            )

    exc = excinfo.value
    assert exc.code == "live_notification_failed"
    assert "saved to inbox" in exc.message
    assert exc.message_id == message_id
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_high_priority_returns_push_transport_warning():
    sender_id, recipient_id = uuid.uuid4(), uuid.uuid4()
    sender = SimpleNamespace(id=sender_id, nickname="Ava", user=None)
    recipient = SimpleNamespace(id=recipient_id)

    db = make_db(
        _first_mock(sender),
        _first_mock(recipient),
    )

    dispatch = NotificationDispatchResult(
        live_dispatched=True,
        warnings=[
            {
                "code": "push_transport_unavailable",
                "message": "Offline push transport is not implemented",
            }
        ],
    )

    with patch.object(
        MessageService,
        "_send_notification",
        new=AsyncMock(return_value=dispatch),
    ):
        _message, warnings = await MessageService.send_message(
            db,
            sender_id=sender_id,
            recipient_id=recipient_id,
            content="urgent hail",
            priority="high",
        )

    assert any(w["code"] == "push_transport_unavailable" for w in warnings)


@pytest.mark.asyncio
async def test_route_send_message_delivery_failure_returns_structured_503():
    secret = "secret-ws-stack-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    request = MessageCreateRequest(
        recipient_id=uuid.uuid4(),
        content="hello",
        priority="normal",
    )
    message_id = uuid.uuid4()

    with patch.object(messages_mod.MessageService, "check_send_rate_limit"):
        with patch.object(
            messages_mod.MessageService,
            "send_message",
            new=AsyncMock(
                side_effect=MessageDeliveryError(
                    "live_notification_failed",
                    "Message was saved to inbox but the live notification could not be delivered.",
                    message_id=message_id,
                )
            ),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await send_message(
                    request=request,
                    current_player=player,
                    db=MagicMock(),
                )

    exc = excinfo.value
    assert exc.status_code == 503
    assert exc.detail == {
        "code": "live_notification_failed",
        "message": (
            "Message was saved to inbox but the live notification could not be delivered."
        ),
        "message_id": str(message_id),
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_route_send_message_high_priority_success_includes_push_warning():
    player = SimpleNamespace(id=uuid.uuid4())
    request = MessageCreateRequest(
        recipient_id=uuid.uuid4(),
        content="priority hail",
        priority="high",
    )
    sent_at = SimpleNamespace(isoformat=lambda: "2026-09-01T00:00:00+00:00")
    message = SimpleNamespace(id=uuid.uuid4(), sent_at=sent_at)
    warnings = [
        {
            "code": "push_transport_unavailable",
            "message": "Offline push transport is not implemented",
        }
    ]

    with patch.object(messages_mod.MessageService, "check_send_rate_limit"):
        with patch.object(
            messages_mod.MessageService,
            "send_message",
            new=AsyncMock(return_value=(message, warnings)),
        ):
            response = await send_message(
                request=request,
                current_player=player,
                db=MagicMock(),
            )

    assert response.message_id == str(message.id)
    assert response.delivery_warnings == warnings


def test_messages_route_maps_message_delivery_error_not_generic_500():
    """Static pin: route has explicit MessageDeliveryError mapping."""
    src = Path(messages_mod.__file__).read_text(encoding="utf-8")
    assert "except MessageDeliveryError" in src
    assert '"code": e.code' in src
    assert "status_code=503" in src
    assert "ERR_MESSAGES_SEND_FAILED" in src
    assert "route_internal_error" in src
    assert 'detail="Failed to send message"' not in src
    assert "Failed to send message: {str(e)}" not in src
