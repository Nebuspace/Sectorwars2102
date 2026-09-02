"""LEG-3873 — audit unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import audit as audit_mod
from src.api.routes.audit import (
    ERR_AUDIT_CREATE_FAILED,
    ERR_AUDIT_FETCH_LOGS_FAILED,
    ERR_AUDIT_LIST_ACTIONS_FAILED,
    ERR_AUDIT_LIST_REVIEW_QUEUE_FAILED,
    ERR_AUDIT_MARK_REVIEWED_FAILED,
    ERR_AUDIT_SECURITY_VIOLATIONS_FAILED,
    ERR_AUDIT_USER_ACTIVITY_FAILED,
    create_audit_log,
    get_audit_logs,
    get_security_violations,
    get_user_activity_summary,
    list_admin_actions,
    list_review_queue,
    mark_action_reviewed,
)


@pytest.mark.asyncio
async def test_create_audit_log_unexpected_returns_structured_500():
    secret = "secret-audit-create-should-not-leak"
    with patch.object(audit_mod.AuditService, "create_audit_log", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await create_audit_log(request={}, admin=SimpleNamespace(id=uuid.uuid4()), db=MagicMock())
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_CREATE_FAILED, "detail": "Failed to create audit log"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_list_admin_actions_unexpected_returns_structured_500():
    secret = "secret-audit-list-actions-should-not-leak"
    db = MagicMock()
    db.query.side_effect = RuntimeError(secret)
    with pytest.raises(HTTPException) as excinfo:
        await list_admin_actions(admin=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_LIST_ACTIONS_FAILED, "detail": "Failed to list admin actions"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_list_review_queue_unexpected_returns_structured_500():
    secret = "secret-audit-review-queue-should-not-leak"
    db = MagicMock()
    db.query.return_value.filter.side_effect = RuntimeError(secret)
    with pytest.raises(HTTPException) as excinfo:
        await list_review_queue(admin=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_LIST_REVIEW_QUEUE_FAILED, "detail": "Failed to list review queue"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_mark_action_reviewed_unexpected_returns_structured_500():
    secret = "secret-audit-mark-reviewed-should-not-leak"
    db = MagicMock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.side_effect = RuntimeError(secret)
    with pytest.raises(HTTPException) as excinfo:
        await mark_action_reviewed(action_id=uuid.uuid4(), admin=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_MARK_REVIEWED_FAILED, "detail": "Failed to mark action reviewed"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_audit_logs_unexpected_returns_structured_500():
    secret = "secret-audit-logs-should-not-leak"
    with patch.object(audit_mod.AuditService, "get_audit_logs", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await get_audit_logs(admin=SimpleNamespace(id=uuid.uuid4()), db=MagicMock())
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_FETCH_LOGS_FAILED, "detail": "Failed to fetch audit logs"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_security_violations_unexpected_returns_structured_500():
    secret = "secret-audit-violations-should-not-leak"
    with patch.object(audit_mod.AuditService, "get_security_violations", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await get_security_violations(admin=SimpleNamespace(id=uuid.uuid4()), db=MagicMock())
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_SECURITY_VIOLATIONS_FAILED, "detail": "Failed to fetch security violations"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_user_activity_summary_unexpected_returns_structured_500():
    secret = "secret-audit-activity-should-not-leak"
    with patch.object(audit_mod.AuditService, "get_user_activity_summary", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await get_user_activity_summary(user_id=uuid.uuid4(), admin=SimpleNamespace(id=uuid.uuid4()), db=MagicMock())
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == {"error_code": ERR_AUDIT_USER_ACTIVITY_FAILED, "detail": "Failed to fetch user activity summary"}
    assert secret not in str(excinfo.value.detail)


def test_audit_http500_catches_are_structured():
    src = Path(audit_mod.__file__).read_text(encoding="utf-8")
    for code in (
        ERR_AUDIT_CREATE_FAILED,
        ERR_AUDIT_LIST_ACTIONS_FAILED,
        ERR_AUDIT_LIST_REVIEW_QUEUE_FAILED,
        ERR_AUDIT_MARK_REVIEWED_FAILED,
        ERR_AUDIT_FETCH_LOGS_FAILED,
        ERR_AUDIT_SECURITY_VIOLATIONS_FAILED,
        ERR_AUDIT_USER_ACTIVITY_FAILED,
    ):
        assert code in src
    assert src.count("route_internal_error(") >= 7
    assert 'detail="Failed to create audit log"' not in src
