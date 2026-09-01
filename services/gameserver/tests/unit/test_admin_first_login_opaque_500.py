"""LEG-3689 — admin_first_login HTTP 500 catches must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_first_login as afl_mod
from src.api.routes.admin_first_login import get_conversation_detail, list_conversations


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-first-login-query-should-not-leak")


@pytest.mark.asyncio
async def test_list_conversations_unexpected_is_opaque_500():
    """LEG-3689 — conversation list catch must not echo raw Exception text."""
    secret = "secret-first-login-query-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await list_conversations(
            skip=0,
            limit=50,
            outcome=None,
            ai_provider=None,
            start_date=None,
            end_date=None,
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch conversations"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_conversation_detail_unexpected_is_opaque_500():
    """LEG-3689 — conversation detail catch must not echo raw Exception text."""
    secret = "secret-first-login-query-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_conversation_detail(
            session_id="00000000-0000-0000-0000-000000000001",
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch conversation detail"
    assert secret not in str(exc.detail)


def test_admin_first_login_http500_catches_have_no_detail_str_e():
    """LEG-3689 — static pin: first-login 500 details stay opaque."""
    src = Path(afl_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to fetch conversations"',
        'detail="Failed to fetch conversation detail"',
    ):
        assert stable in src
    assert "Failed to fetch conversations: {str(e)}" not in src
    assert "Failed to fetch conversation detail: {str(e)}" not in src
