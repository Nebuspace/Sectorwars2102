"""LEG-3907 densify — admin_first_login structured 500s."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.routes import admin_first_login as afl_mod
from src.api.routes.admin_first_login import (
    ERR_ADMIN_FIRST_LOGIN_DETAIL_FAILED,
    ERR_ADMIN_FIRST_LOGIN_LIST_FAILED,
    get_conversation_detail,
    list_conversations,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-first-login-query-should-not-leak")


@pytest.mark.asyncio
async def test_list_conversations_returns_structured_500():
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
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {
        "error_code": ERR_ADMIN_FIRST_LOGIN_LIST_FAILED,
        "detail": "Failed to fetch conversations",
    }
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_conversation_detail_returns_structured_500():
    secret = "secret-first-login-query-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_conversation_detail(
            session_id="00000000-0000-0000-0000-000000000001",
            db=_BoomDB(),
        )
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {
        "error_code": ERR_ADMIN_FIRST_LOGIN_DETAIL_FAILED,
        "detail": "Failed to fetch conversation detail",
    }
    assert secret not in str(excinfo.value.detail)


def test_admin_first_login_http500_is_structured():
    src = Path(afl_mod.__file__).read_text(encoding="utf-8")
    assert ERR_ADMIN_FIRST_LOGIN_LIST_FAILED in src
    assert ERR_ADMIN_FIRST_LOGIN_DETAIL_FAILED in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch conversations"' not in src
