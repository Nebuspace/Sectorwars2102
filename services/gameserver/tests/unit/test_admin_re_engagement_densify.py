"""LEG-3858 — admin_re_engagement unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_re_engagement as mod
from src.api.routes.admin_re_engagement import (
    ReEngagementStatusUpdate,
    list_re_engagement_queue,
    re_engagement_summary,
    update_re_engagement_status,
)


@pytest.mark.asyncio
async def test_re_engagement_summary_unexpected_returns_structured_500():
    secret = "secret-re-engagement-summary-should-not-leak"
    db = MagicMock()
    db.query.return_value.group_by.return_value.all.side_effect = RuntimeError(secret)

    with pytest.raises(HTTPException) as excinfo:
        await re_engagement_summary(admin=SimpleNamespace(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_RE_ENGAGEMENT_SUMMARY_FAILED",
        "detail": "Failed to fetch re-engagement summary",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_list_re_engagement_queue_unexpected_returns_structured_500():
    secret = "secret-re-engagement-list-should-not-leak"
    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.count.side_effect = (
        RuntimeError(secret)
    )

    with pytest.raises(HTTPException) as excinfo:
        await list_re_engagement_queue(
            status_filter="OPEN",
            limit=100,
            offset=0,
            admin=SimpleNamespace(),
            db=db,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_RE_ENGAGEMENT_LIST_FAILED",
        "detail": "Failed to fetch re-engagement queue",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_re_engagement_status_unexpected_returns_structured_500():
    secret = "secret-re-engagement-patch-should-not-leak"
    entry_id = uuid4()
    row = SimpleNamespace(
        id=entry_id,
        player_id=uuid4(),
        player=SimpleNamespace(nickname="tester"),
        signals=[],
        signal_detail={},
        status="OPEN",
        computed_at=None,
        computed_day=None,
        resolved_at=None,
    )

    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.first.return_value = row
    db.commit.side_effect = RuntimeError(secret)

    body = ReEngagementStatusUpdate(status="CONTACTED", note="called")
    with patch.object(mod, "log_admin_action"):
        with pytest.raises(HTTPException) as excinfo:
            await update_re_engagement_status(
                entry_id=entry_id,
                body=body,
                admin=SimpleNamespace(id=uuid4()),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_RE_ENGAGEMENT_UPDATE_FAILED",
        "detail": "Failed to update re-engagement status",
    }
    assert secret not in str(exc.detail)


def test_admin_re_engagement_http500_catches_are_structured():
    """LEG-3858 — static pin: re-engagement 500 catch paths emit error_code + detail."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_RE_ENGAGEMENT_SUMMARY_FAILED",
        "ERR_ADMIN_RE_ENGAGEMENT_LIST_FAILED",
        "ERR_ADMIN_RE_ENGAGEMENT_UPDATE_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch re-engagement summary"' not in src
    assert 'detail="Failed to fetch re-engagement queue"' not in src
    assert 'detail="Failed to update re-engagement status"' not in src
