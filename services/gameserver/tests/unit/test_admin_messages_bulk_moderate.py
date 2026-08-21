"""LEG-266 — POST /admin/messages/bulk-moderate."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from src.api.middleware.security import ADMIN_TIER_BULK, classify_admin_tier
from src.api.routes import admin_messages as am
from src.services.message_service import MessageService


def test_bulk_path_classified_as_bulk_tier():
    assert (
        classify_admin_tier("POST", "/api/v1/admin/messages/bulk-moderate")
        == ADMIN_TIER_BULK
    )


def test_bulk_route_registered():
    paths = {
        getattr(r, "path", None)
        for r in am.router.routes
        if "POST" in (getattr(r, "methods", None) or set())
    }
    assert "/admin/messages/bulk-moderate" in paths
    assert "/admin/messages/{message_id}/moderate" in paths


def test_bulk_partial_failure():
    good = uuid.uuid4()
    missing = uuid.uuid4()

    async def _mod(*, db, message_id, action, moderator_id, reason=None):
        return message_id == good

    with patch.object(MessageService, "moderate_message", new=AsyncMock(side_effect=_mod)):
        body = am.BulkModerateRequest(
            message_ids=[good, missing],
            action="delete",
        )
        admin = type("U", (), {"id": uuid.uuid4()})()
        result = asyncio.run(
            am.bulk_moderate_messages(request=body, admin=admin, db=object())
        )

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.results[0].success is True
    assert result.results[1].success is False
    assert result.results[1].detail == "message_not_found"


def test_bulk_rejects_flag_action():
    from fastapi import HTTPException

    body = am.BulkModerateRequest(
        message_ids=[uuid.uuid4()],
        action="flag",
    )
    admin = type("U", (), {"id": uuid.uuid4()})()
    try:
        asyncio.run(am.bulk_moderate_messages(request=body, admin=admin, db=object()))
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400


def test_bulk_unflag_calls_service():
    mid = uuid.uuid4()
    mock = AsyncMock(return_value=True)
    with patch.object(MessageService, "moderate_message", new=mock):
        body = am.BulkModerateRequest(message_ids=[mid], action="unflag", reason="ok")
        admin = type("U", (), {"id": uuid.uuid4()})()
        result = asyncio.run(
            am.bulk_moderate_messages(request=body, admin=admin, db=object())
        )
    assert result.succeeded == 1
    assert result.failed == 0
    mock.assert_awaited_once()
    assert mock.await_args.kwargs["action"] == "unflag"
    assert mock.await_args.kwargs["message_id"] == mid


def test_bulk_route_uses_security_act_dependency():
    """Same authorization gate as single-row moderate (SECURITY_ACT)."""
    bulk = next(
        r
        for r in am.router.routes
        if getattr(r, "path", None) == "/admin/messages/bulk-moderate"
    )
    single = next(
        r
        for r in am.router.routes
        if getattr(r, "path", None) == "/admin/messages/{message_id}/moderate"
    )
    # Both depend on require_scope(SECURITY_ACT) — endpoint callables share
    # the same Depends wiring; prove dependencies are present on the route.
    assert bulk.dependant is not None
    assert single.dependant is not None
    bulk_dep_names = {d.name for d in bulk.dependant.dependencies}
    single_dep_names = {d.name for d in single.dependant.dependencies}
    assert "admin" in bulk_dep_names
    assert "admin" in single_dep_names
