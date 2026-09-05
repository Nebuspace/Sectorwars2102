"""LEG-3704 — admin_players bulk-operation must not echo catastrophic Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_players as ap_mod
from src.api.routes.admin_players import (
    BulkOperationParameters,
    BulkOperationRequest,
    bulk_player_operation,
)


@pytest.mark.asyncio
async def test_bulk_player_operation_commit_failure_is_opaque_500():
    secret = "secret-bulk-commit-should-not-leak"
    player = SimpleNamespace(id=uuid4(), credits=100, turns=10, username="tester")
    player_id = str(player.id)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player
    db.commit.side_effect = RuntimeError(secret)

    request = BulkOperationRequest(
        player_ids=[player_id],
        operation="CREDIT_ADJUST",
        parameters=BulkOperationParameters(amount=10, reason="test bulk"),
    )

    with patch.object(ap_mod, "user_has_active_scope", return_value=True):
        with patch.object(ap_mod, "log_admin_action"):
            with pytest.raises(HTTPException) as excinfo:
                await bulk_player_operation(
                    request=request,
                    admin=SimpleNamespace(id=1, username="admin"),
                    db=db,
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_PLAYERS_BULK_FAILED",
        "detail": "Bulk player operation failed",
    }
    assert secret not in str(exc.detail)


def test_admin_players_bulk_http500_is_opaque():
    """LEG-3704 — static pin: bulk route 500 detail stays opaque."""
    src = Path(ap_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    assert "ERR_ADMIN_PLAYERS_BULK_FAILED" in src
    assert 'detail="Bulk player operation failed"' not in src
    assert "Bulk player operation failed: {str(e)}" not in src
