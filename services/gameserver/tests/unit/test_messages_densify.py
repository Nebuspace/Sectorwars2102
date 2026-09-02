"""LEG-3877 densify — messages.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3794 planets / LEG-3711 admin_messages opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import messages as messages_mod
from src.api.routes.messages import (
    ERR_MESSAGES_CONVERSATIONS_FAILED,
    ERR_MESSAGES_DELETE_FAILED,
    ERR_MESSAGES_FLAG_FAILED,
    ERR_MESSAGES_INBOX_FAILED,
    ERR_MESSAGES_MARK_READ_FAILED,
    ERR_MESSAGES_SEND_FAILED,
    ERR_MESSAGES_TEAM_FAILED,
    MessageCreateRequest,
    delete_message,
    flag_message,
    get_conversations,
    get_inbox,
    get_team_messages,
    mark_message_read,
    send_message,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_send_message_returns_structured_500():
    secret = "secret-send-message-should-not-leak"
    request = MessageCreateRequest(
        recipient_id=uuid.uuid4(),
        content="hello",
    )

    with patch.object(
        messages_mod.MessageService,
        "check_send_rate_limit",
    ), patch.object(
        messages_mod.MessageService,
        "send_message",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await send_message(
                request=request,
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_SEND_FAILED",
        "detail": "Failed to send message",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_inbox_returns_structured_500():
    secret = "secret-inbox-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "get_inbox",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_inbox(
                page=1,
                unread_only=False,
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_INBOX_FAILED",
        "detail": "Failed to load inbox",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_team_messages_returns_structured_500():
    secret = "secret-team-messages-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "get_team_messages",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_team_messages(
                team_id=uuid.uuid4(),
                page=1,
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_TEAM_FAILED",
        "detail": "Failed to load team messages",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_mark_message_read_returns_structured_500():
    secret = "secret-mark-read-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "mark_as_read",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await mark_message_read(
                message_id=uuid.uuid4(),
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_MARK_READ_FAILED",
        "detail": "Failed to mark message as read",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_delete_message_returns_structured_500():
    secret = "secret-delete-message-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "delete_message",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await delete_message(
                message_id=uuid.uuid4(),
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_DELETE_FAILED",
        "detail": "Failed to delete message",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_conversations_returns_structured_500():
    secret = "secret-conversations-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "get_conversations",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_conversations(
                page=1,
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_CONVERSATIONS_FAILED",
        "detail": "Failed to load conversations",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_flag_message_returns_structured_500():
    secret = "secret-flag-message-should-not-leak"

    with patch.object(
        messages_mod.MessageService,
        "flag_message",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await flag_message(
                message_id=uuid.uuid4(),
                reason="spam content here",
                current_player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_MESSAGES_FLAG_FAILED",
        "detail": "Failed to flag message",
    }
    assert secret not in str(exc.detail)


def test_messages_http500_catches_are_structured():
    src = Path(messages_mod.__file__).read_text(encoding="utf-8")
    for code in (
        ERR_MESSAGES_SEND_FAILED,
        ERR_MESSAGES_INBOX_FAILED,
        ERR_MESSAGES_TEAM_FAILED,
        ERR_MESSAGES_MARK_READ_FAILED,
        ERR_MESSAGES_DELETE_FAILED,
        ERR_MESSAGES_CONVERSATIONS_FAILED,
        ERR_MESSAGES_FLAG_FAILED,
    ):
        assert code in src
    assert src.count("route_internal_error(") >= 7
    assert 'detail="Failed to send message"' not in src
