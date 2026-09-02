"""LEG-3727 — contract dispute arbitration routes must not echo Exception text."""

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
from src.models.contract import ContractDisputeResolution


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-dispute-list-should-not-leak")


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    attempt = MagicMock()
    yield attempt


@pytest.mark.asyncio
async def test_list_disputed_contracts_boom_is_opaque_500():
    with pytest.raises(HTTPException) as excinfo:
        await list_disputed_contracts(
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch contract disputes"
    assert "secret-dispute-list-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_resolve_contract_dispute_boom_is_opaque_500():
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
    assert exc.detail == "Failed to resolve contract dispute"
    assert secret not in str(exc.detail)


def test_admin_contract_disputes_http500_is_opaque():
    """LEG-3727 — static pin: dispute route 500 details stay opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to fetch contract disputes"' in src
    assert 'detail="Failed to resolve contract dispute"' in src
    assert "Failed to fetch contract disputes: {str(e)}" not in src
    assert "Failed to resolve contract dispute: {str(e)}" not in src
