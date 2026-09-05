"""LEG-903 — POST /admin/players/bulk-operation."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.middleware.security import ADMIN_TIER_BULK, classify_admin_tier
from src.api.routes import admin_players as ap
from src.auth.admin_scopes import (
    PLAYERS_ADJUST_CREDITS,
    PLAYERS_SUSPEND,
    PLAYERS_VIEW,
)


def test_bulk_path_classified_as_bulk_tier():
    assert (
        classify_admin_tier("POST", "/api/v1/admin/players/bulk-operation")
        == ADMIN_TIER_BULK
    )


def test_bulk_route_registered():
    paths = {
        getattr(r, "path", None)
        for r in ap.router.routes
        if "POST" in (getattr(r, "methods", None) or set())
    }
    assert "/players/bulk-operation" in paths


def test_bulk_rejects_missing_amount_for_credit_adjust():
    body = ap.BulkOperationRequest(
        player_ids=[str(uuid.uuid4())],
        operation="CREDIT_ADJUST",
        parameters=ap.BulkOperationParameters(reason="test"),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()

    def _has_scope(_db, _uid, scope):
        return scope in (PLAYERS_VIEW, PLAYERS_ADJUST_CREDITS)

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ap.bulk_player_operation(request=body, admin=admin, db=db))
    assert exc.value.status_code == 400
    assert "amount" in exc.value.detail


def test_bulk_validation_missing_reason():
    with pytest.raises(Exception):
        ap.BulkOperationParameters(reason="")


def test_bulk_unauthorized_missing_scope():
    pid = str(uuid.uuid4())
    body = ap.BulkOperationRequest(
        player_ids=[pid],
        operation="CREDIT_ADJUST",
        parameters=ap.BulkOperationParameters(amount=100, reason="grant"),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()

    def _has_scope(_db, _uid, scope):
        return scope == PLAYERS_VIEW

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                ap.bulk_player_operation(request=body, admin=admin, db=db)
            )
    assert exc.value.status_code == 403
    assert PLAYERS_ADJUST_CREDITS in exc.value.detail


def test_bulk_credit_adjust_success():
    pid = uuid.uuid4()
    player = MagicMock(id=pid, credits=1000, turns=50)
    body = ap.BulkOperationRequest(
        player_ids=[str(pid)],
        operation="CREDIT_ADJUST",
        parameters=ap.BulkOperationParameters(amount=250, reason="event payout"),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player

    def _has_scope(_db, _uid, scope):
        return scope in (PLAYERS_VIEW, PLAYERS_ADJUST_CREDITS)

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        with patch.object(ap, "log_admin_action"):
            result = asyncio.run(
                ap.bulk_player_operation(request=body, admin=admin, db=db)
            )

    assert result.applied == 1
    assert result.rejected == 0
    assert player.credits == 1250
    db.commit.assert_called_once()


def test_bulk_status_change_requires_suspend_scope():
    pid = uuid.uuid4()
    player = MagicMock(id=pid, credits=0, turns=0)
    body = ap.BulkOperationRequest(
        player_ids=[str(pid)],
        operation="STATUS_CHANGE",
        parameters=ap.BulkOperationParameters(
            new_status="suspended", reason="abuse"
        ),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player

    def _has_scope(_db, _uid, scope):
        return scope == PLAYERS_VIEW

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                ap.bulk_player_operation(request=body, admin=admin, db=db)
            )
    assert exc.value.status_code == 403
    assert PLAYERS_SUSPEND in exc.value.detail


def test_bulk_partial_failure_player_not_found():
    missing = uuid.uuid4()
    body = ap.BulkOperationRequest(
        player_ids=[str(missing)],
        operation="TURN_GRANT",
        parameters=ap.BulkOperationParameters(amount=10, reason="bonus"),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    def _has_scope(_db, _uid, scope):
        return scope in (PLAYERS_VIEW, PLAYERS_ADJUST_CREDITS)

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        result = asyncio.run(
            ap.bulk_player_operation(request=body, admin=admin, db=db)
        )

    assert result.applied == 0
    assert result.rejected == 1
    assert result.results[0].detail == "player_not_found"


def test_bulk_reputation_adjust_calls_faction_service():
    pid = uuid.uuid4()
    faction_id = uuid.uuid4()
    player = MagicMock(id=pid, credits=0, turns=0)
    faction = MagicMock(id=faction_id, name="Concord")
    rep = MagicMock(current_value=10)

    body = ap.BulkOperationRequest(
        player_ids=[str(pid)],
        operation="REPUTATION_ADJUST",
        parameters=ap.BulkOperationParameters(
            reason="rebalance",
            reputation_changes=[
                ap.ReputationChangeItem(faction="Concord", new_value=50)
            ],
        ),
    )
    admin = MagicMock(id=uuid.uuid4(), username="admin")
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model.__name__ == "Player":
            q.filter.return_value.first.return_value = player
        elif model.__name__ == "Faction":
            q.filter.return_value.first.return_value = faction
        elif model.__name__ == "Reputation":
            q.filter.return_value.first.return_value = rep
        return q

    db.query.side_effect = _query
    mock_update = AsyncMock()

    def _has_scope(_db, _uid, scope):
        return True

    with patch.object(ap, "user_has_active_scope", side_effect=_has_scope):
        with patch.object(ap, "log_admin_action"):
            with patch.object(
                ap.FactionService,
                "update_reputation",
                new=mock_update,
            ):
                result = asyncio.run(
                    ap.bulk_player_operation(request=body, admin=admin, db=db)
                )

    assert result.applied == 1
    mock_update.assert_awaited_once()
    assert mock_update.await_args.kwargs["change"] == 40
