"""LEG-3594 — messages.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3561 admin_messages / LEG-3569 claim_ship / LEG-3570 colonization opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import messages as msg_mod
from src.api.routes.messages import MessageCreateRequest, get_inbox, send_message


@pytest.mark.asyncio
async def test_send_message_unexpected_is_opaque_500():
    """Outer send_message catch must not echo raw Exception text."""
    secret = "secret-send-message-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())
    request = MessageCreateRequest(
        recipient_id=uuid.uuid4(),
        content="hello",
    )

    with patch.object(msg_mod.MessageService, "check_send_rate_limit"):
        with patch.object(
            msg_mod.MessageService,
            "send_message",
            side_effect=RuntimeError(secret),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await send_message(
                    request=request,
                    current_player=player,
                    db=MagicMock(),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to send message"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_inbox_unexpected_is_opaque_500():
    """get_inbox catch must not echo raw Exception text."""
    secret = "secret-inbox-should-not-leak"
    player = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        msg_mod.MessageService,
        "get_inbox",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_inbox(current_player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to load inbox"
    assert secret not in str(exc.detail)


def test_messages_http500_catches_have_no_detail_str_e():
    """LEG-3594 — static pin: all seven HTTP 500 catch paths stay opaque."""
    src = Path(msg_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to send message"',
        'detail="Failed to load inbox"',
        'detail="Failed to load team messages"',
        'detail="Failed to mark message as read"',
        'detail="Failed to delete message"',
        'detail="Failed to load conversations"',
        'detail="Failed to flag message"',
    ):
        assert stable in src
    assert "Failed to send message: {str(e)}" not in src
    assert "Failed to load inbox: {str(e)}" not in src
    assert "Failed to load team messages: {str(e)}" not in src
    assert "Failed to mark message as read: {str(e)}" not in src
    assert "Failed to delete message: {str(e)}" not in src
    assert "Failed to load conversations: {str(e)}" not in src
    assert "Failed to flag message: {str(e)}" not in src
