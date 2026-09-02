"""LEG-3728 — canon moderation actions must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_moderation_messages as mod
from src.api.routes.admin_moderation_messages import (
    _dispatch,
    block_flagged_message,
)


@pytest.mark.asyncio
async def test_dispatch_accept_boom_is_opaque_500():
    secret = "secret-moderation-accept-should-not-leak"
    with patch.object(
        mod.MessageService,
        "moderation_canon_action",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await _dispatch(
                message_id=uuid4(),
                action="accept",
                reason=None,
                admin=SimpleNamespace(id=uuid4()),
                db=SimpleNamespace(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to process moderation action"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_block_route_boom_is_opaque_500():
    secret = "secret-moderation-block-should-not-leak"
    with patch.object(
        mod.MessageService,
        "moderation_canon_action",
        new_callable=AsyncMock,
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await block_flagged_message(
                message_id=uuid4(),
                body=None,
                admin=SimpleNamespace(id=uuid4()),
                db=SimpleNamespace(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to process moderation action"
    assert secret not in str(exc.detail)


def test_admin_moderation_messages_http500_is_opaque():
    """LEG-3728 — static pin: moderation dispatch 500 detail stays opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to process moderation action"' in src
    assert "Failed to process moderation action: {str(e)}" not in src
