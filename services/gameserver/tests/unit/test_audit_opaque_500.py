"""LEG-3581 — audit.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3561 admin_messages / LEG-3569 claim_ship / LEG-3570 colonization opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import audit as audit_mod
from src.api.routes.audit import (
    create_audit_log,
    get_audit_logs,
    get_security_violations,
    get_user_activity_summary,
)


@pytest.mark.asyncio
async def test_create_audit_log_unexpected_is_opaque_500():
    """Outer create_audit_log catch must not echo raw Exception text."""
    secret = "secret-audit-create-should-not-leak"
    admin = SimpleNamespace(id=uuid.uuid4())

    with patch.object(audit_mod.AuditService, "create_audit_log", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await create_audit_log(request={}, admin=admin, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_AUDIT_CREATE_FAILED",
        "detail": "Failed to create audit log",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_audit_logs_unexpected_is_opaque_500():
    """get_audit_logs catch must not echo raw Exception text."""
    secret = "secret-audit-logs-should-not-leak"
    admin = SimpleNamespace(id=uuid.uuid4())

    with patch.object(audit_mod.AuditService, "get_audit_logs", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await get_audit_logs(admin=admin, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_AUDIT_FETCH_LOGS_FAILED",
        "detail": "Failed to fetch audit logs",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_security_violations_unexpected_is_opaque_500():
    """get_security_violations catch must not echo raw Exception text."""
    secret = "secret-audit-violations-should-not-leak"
    admin = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        audit_mod.AuditService, "get_security_violations", side_effect=RuntimeError(secret)
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_security_violations(admin=admin, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_AUDIT_SECURITY_VIOLATIONS_FAILED",
        "detail": "Failed to fetch security violations",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_user_activity_summary_unexpected_is_opaque_500():
    """get_user_activity_summary catch must not echo raw Exception text."""
    secret = "secret-audit-activity-should-not-leak"
    admin = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        audit_mod.AuditService, "get_user_activity_summary", side_effect=RuntimeError(secret)
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_user_activity_summary(user_id=uuid.uuid4(), admin=admin, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_AUDIT_USER_ACTIVITY_FAILED",
        "detail": "Failed to fetch user activity summary",
    }
    assert secret not in str(exc.detail)


def test_audit_http500_catches_have_no_detail_str_e():
    """Static pin: the four HTTP 500 catch paths stay opaque (no str(e))."""
    src = Path(audit_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    assert "ERR_AUDIT_USER_ACTIVITY_FAILED" in src
    assert "ERR_AUDIT_SECURITY_VIOLATIONS_FAILED" in src
    assert "ERR_AUDIT_FETCH_LOGS_FAILED" in src
    assert "ERR_AUDIT_MARK_REVIEWED_FAILED" in src
    assert "ERR_AUDIT_LIST_REVIEW_QUEUE_FAILED" in src
    assert "ERR_AUDIT_LIST_ACTIONS_FAILED" in src
    assert "ERR_AUDIT_CREATE_FAILED" in src
    assert "detail=str(e)" not in src
