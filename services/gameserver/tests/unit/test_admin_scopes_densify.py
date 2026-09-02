"""LEG-3833 — admin_scopes grant/revoke unexpected failures return structured 500s."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_scopes as mod
from src.api.routes.admin_scopes import (
    ERR_SCOPE_GRANT_FAILED,
    ERR_SCOPE_REVOKE_FAILED,
    ScopeMutationRequest,
    grant_scope,
    revoke_scope,
)


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    attempt = MagicMock()
    yield attempt


@pytest.mark.asyncio
async def test_grant_scope_boom_returns_structured_500():
    secret = "secret-scope-grant-should-not-leak"
    target = SimpleNamespace(id=uuid4(), is_admin=False)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = target
    body = ScopeMutationRequest(user_id=target.id, scope="admin.players.view")

    with patch.object(mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(
            mod, "grant_scope_to_user", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await grant_scope(
                    body=body,
                    db=db,
                    actor=SimpleNamespace(id=uuid4()),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_SCOPE_GRANT_FAILED,
        "detail": "Grant failed",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_revoke_scope_boom_returns_structured_500():
    secret = "secret-scope-revoke-should-not-leak"
    target = SimpleNamespace(id=uuid4(), is_admin=True)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = target
    body = ScopeMutationRequest(user_id=target.id, scope="admin.players.view")

    with patch.object(mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(
            mod, "revoke_scope_from_user", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await revoke_scope(
                    body=body,
                    db=db,
                    actor=SimpleNamespace(id=uuid4()),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": ERR_SCOPE_REVOKE_FAILED,
        "detail": "Revoke failed",
    }
    assert secret not in str(exc.detail)


def test_admin_scopes_grant_revoke_densify_is_structured():
    """LEG-3833 — static pin: grant/revoke catch paths emit error_code + detail."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert ERR_SCOPE_GRANT_FAILED in src
    assert ERR_SCOPE_REVOKE_FAILED in src
    assert '"error_code": ERR_SCOPE_GRANT_FAILED' in src
    assert '"error_code": ERR_SCOPE_REVOKE_FAILED' in src
    assert 'detail="Grant failed"' not in src
    assert 'detail="Revoke failed"' not in src
    assert ') from exc' not in src
