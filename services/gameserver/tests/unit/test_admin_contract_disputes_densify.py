"""LEG-3857 — admin_contract_disputes unexpected failures return structured 500s."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_contract_disputes as mod
from src.api.routes.admin_contract_disputes import (
    ResolveDisputeRequest,
    list_disputed_contracts,
    resolve_contract_dispute,
)


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    attempt = MagicMock()
    yield attempt


@pytest.mark.asyncio
async def test_list_disputed_contracts_unexpected_returns_structured_500():
    secret = "secret-dispute-list-should-not-leak"
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.side_effect = (
        RuntimeError(secret)
    )

    with pytest.raises(HTTPException) as excinfo:
        await list_disputed_contracts(admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONTRACT_DISPUTES_LIST_FAILED",
        "detail": "Failed to fetch contract disputes",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_resolve_contract_dispute_unexpected_returns_structured_500():
    secret = "secret-dispute-resolve-should-not-leak"
    body = ResolveDisputeRequest(outcome="full_payout", notes="test")

    with patch.object(mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(mod, "resolve_dispute", side_effect=RuntimeError(secret)):
            with pytest.raises(HTTPException) as excinfo:
                await resolve_contract_dispute(
                    contract_id=str(uuid4()),
                    body=body,
                    admin=SimpleNamespace(id=uuid4()),
                    db=MagicMock(),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONTRACT_DISPUTES_RESOLVE_FAILED",
        "detail": "Failed to resolve contract dispute",
    }
    assert secret not in str(exc.detail)


def test_admin_contract_disputes_http500_catches_are_structured():
    """LEG-3857 — static pin: dispute route 500 catch paths emit error_code + detail."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_CONTRACT_DISPUTES_LIST_FAILED",
        "ERR_ADMIN_CONTRACT_DISPUTES_RESOLVE_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch contract disputes"' not in src
    assert 'detail="Failed to resolve contract dispute"' not in src
