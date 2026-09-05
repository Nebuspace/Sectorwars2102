"""LEG-3715 — admin subscription grant/revoke must not echo Exception text."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_subscriptions as mod
from src.api.routes.admin_subscriptions import (
    GcMutationRequest,
    grant_galactic_citizen,
    revoke_galactic_citizen,
)


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    attempt = MagicMock()
    yield attempt


@pytest.mark.asyncio
async def test_grant_galactic_citizen_boom_is_opaque_500():
    secret = "secret-gc-grant-should-not-leak"
    player = SimpleNamespace(id=uuid4(), user_id=None, user=None)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player
    body = GcMutationRequest(reason="comp")

    with patch.object(mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(
            mod, "manual_grant_galactic_citizen", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await grant_galactic_citizen(
                    player_id=str(player.id),
                    body=body,
                    actor=SimpleNamespace(id=uuid4()),
                    db=db,
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to grant galactic citizenship"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_revoke_galactic_citizen_boom_is_opaque_500():
    secret = "secret-gc-revoke-should-not-leak"
    player = SimpleNamespace(id=uuid4(), user_id=None, user=None)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player
    body = GcMutationRequest(reason="clawback")

    with patch.object(mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(
            mod, "manual_revoke_galactic_citizen", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await revoke_galactic_citizen(
                    player_id=str(player.id),
                    body=body,
                    actor=SimpleNamespace(id=uuid4()),
                    db=db,
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to revoke galactic citizenship"
    assert secret not in str(exc.detail)


def test_admin_subscriptions_http500_is_opaque():
    """LEG-3715 — static pin: GC grant/revoke 500 details stay opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to grant galactic citizenship"' in src
    assert 'detail="Failed to revoke galactic citizenship"' in src
