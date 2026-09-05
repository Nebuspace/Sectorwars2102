"""LEG-3805 — messages.py HTTP 500 catches must not echo Exception text.

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
async def test_send_message_unexpected_is_opaque_500():
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
async def test_get_inbox_unexpected_is_opaque_500():
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
async def test_get_team_messages_unexpected_is_opaque_500():
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
async def test_mark_message_read_unexpected_is_opaque_500():
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
async def test_delete_message_unexpected_is_opaque_500():
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
async def test_get_conversations_unexpected_is_opaque_500():
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
async def test_flag_message_unexpected_is_opaque_500():
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


def test_messages_http500_catches_have_no_detail_str_e():
    """LEG-3805 — static pin: all seven HTTP 500 catch paths stay opaque."""
    src = Path(messages_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    assert "ERR_MESSAGES_FLAG_FAILED" in src
    assert "ERR_MESSAGES_CONVERSATIONS_FAILED" in src
    assert "ERR_MESSAGES_DELETE_FAILED" in src
    assert "ERR_MESSAGES_MARK_READ_FAILED" in src
    assert "ERR_MESSAGES_TEAM_FAILED" in src
    assert "ERR_MESSAGES_INBOX_FAILED" in src
    assert "ERR_MESSAGES_SEND_FAILED" in src
    assert "Failed to send message: {str(e)}" not in src
    assert "Failed to load inbox: {str(e)}" not in src
    assert "Failed to load team messages: {str(e)}" not in src
    assert "Failed to mark message as read: {str(e)}" not in src
    assert "Failed to delete message: {str(e)}" not in src
    assert "Failed to load conversations: {str(e)}" not in src
    assert "Failed to flag message: {str(e)}" not in src
