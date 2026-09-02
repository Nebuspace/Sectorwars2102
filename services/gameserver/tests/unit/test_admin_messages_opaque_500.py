"""LEG-3711 — admin message moderation routes must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_messages as mod
from src.api.routes.admin_messages import (
    ModerateMessageRequest,
    _list_admin_messages,
    moderate_message,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-messages-list-should-not-leak")


@pytest.mark.asyncio
async def test_list_admin_messages_boom_is_opaque_500():
    with pytest.raises(HTTPException) as excinfo:
        await _list_admin_messages(page=1, flagged=True, db=_BoomDB())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to list admin messages"
    assert "secret-admin-messages-list-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_moderate_message_boom_is_opaque_500():
    secret = "secret-admin-moderate-should-not-leak"
    request = ModerateMessageRequest(action="flag", reason="spam")

    with patch.object(
        mod.MessageService,
        "moderate_message",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await moderate_message(
                message_id=uuid4(),
                request=request,
                admin=SimpleNamespace(id=uuid4()),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to moderate message"
    assert secret not in str(exc.detail)


def test_admin_messages_http500_is_opaque():
    """LEG-3711 — static pin: admin message route 500 details stay opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to list admin messages"' in src
    assert 'detail="Failed to moderate message"' in src
    assert 'detail="Failed to load message statistics"' in src
