"""RBAC Phase E-5 — admin_action_attempt helper + first-cut wrap set.

DB-free source asserts + in-memory smoke for blocked-attempt own-commit.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import HTTPException

from src.auth.admin_scopes import HIGH_IMPACT_SCOPES, SCOPES_REVOKE
from src.services.admin_action_attempt import (
    E5_WRAPPED_ROUTES,
    admin_action_attempt,
    sanitize_failure_reason,
)

_GS_ROOT = Path(__file__).resolve().parents[2]
_SCOPES_SRC = (_GS_ROOT / "src" / "api" / "routes" / "admin_scopes.py").read_text()
_DISPUTES_SRC = (
    _GS_ROOT / "src" / "api" / "routes" / "admin_contract_disputes.py"
).read_text()
_ATTEMPT_SRC = (
    _GS_ROOT / "src" / "services" / "admin_action_attempt.py"
).read_text()


class TestSanitizeFailureReason:
    def test_redacts_secretish_and_truncates(self):
        raw = "password=hunter2 and token: abcdef " + ("x" * 600)
        out = sanitize_failure_reason(raw, max_len=80)
        assert "hunter2" not in out
        assert "[redacted]" in out
        assert len(out) <= 80

    def test_collapses_whitespace(self):
        assert sanitize_failure_reason("a\n\nb") == "a b"


class TestE5WrappedRouteSet:
    """No silent partial coverage — first cut is explicit."""

    def test_wrapped_set_is_high_impact_mutations_only(self):
        assert E5_WRAPPED_ROUTES == frozenset(
            {
                "POST /admin/scopes/grant",
                "POST /admin/scopes/revoke",
                "POST /admin/contracts/{contract_id}/resolve-dispute",
            }
        )
        assert SCOPES_REVOKE in HIGH_IMPACT_SCOPES

    def test_grant_revoke_use_attempt_helper(self):
        assert "admin_action_attempt" in _SCOPES_SRC
        grant = _SCOPES_SRC.split('@router.post("/grant"', 1)[1].split(
            '@router.post("/revoke"', 1
        )[0]
        revoke = _SCOPES_SRC.split('@router.post("/revoke"', 1)[1]
        assert "admin_action_attempt" in grant
        assert "attempt.succeed" in grant
        assert "admin_action_attempt" in revoke
        assert "attempt.succeed" in revoke
        # Success logging moved out of helpers — no double-log.
        helper_grant = _SCOPES_SRC.split("def grant_scope_to_user", 1)[1].split(
            "def revoke_scope_from_user", 1
        )[0]
        helper_revoke = _SCOPES_SRC.split("def revoke_scope_from_user", 1)[1].split(
            "@router.get", 1
        )[0]
        assert "log_admin_action" not in helper_grant
        assert "log_admin_action" not in helper_revoke
        assert "_log_action" not in helper_grant
        assert "_log_action" not in helper_revoke

    def test_disputes_resolve_uses_attempt_helper(self):
        block = _DISPUTES_SRC.split(
            '@router.post("/{contract_id}/resolve-dispute"', 1
        )[1]
        assert "admin_action_attempt" in block
        assert "attempt.succeed" in block
        assert "log_admin_action" not in block

    def test_helper_documents_dual_commit_policy(self):
        assert "commits the log alone" in _ATTEMPT_SRC.lower() or "commit the log alone" in _ATTEMPT_SRC.lower()
        assert '"blocked"' in _ATTEMPT_SRC
        assert '"failed"' in _ATTEMPT_SRC


class TestBlockedAttemptOwnCommit:
    def test_http_409_logs_blocked_and_commits(self):
        db = MagicMock()
        actor = MagicMock()
        actor.id = uuid.uuid4()

        logged = {}

        def _capture_add(row):
            logged["row"] = row

        db.add.side_effect = _capture_add

        # Patch log_admin_action path by importing and using real helper with mocked db
        from src.models.admin_action_log import AdminActionLog

        try:
            with admin_action_attempt(
                db,
                actor=actor,
                scope_used=SCOPES_REVOKE,
                action="scope_revoke",
                target_type="user",
                target_id=str(uuid.uuid4()),
                payload={"scope": "admin.audit.view"},
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Cannot revoke last system-wide holder of admin.audit.view",
                )
        except HTTPException as exc:
            assert exc.status_code == 409

        db.rollback.assert_called()
        db.commit.assert_called()
        db.add.assert_called()
        row = db.add.call_args[0][0]
        assert isinstance(row, AdminActionLog)
        assert row.result == "blocked"
        assert row.scope_used == SCOPES_REVOKE
        assert "last system-wide holder" in (row.failure_reason or "")
        assert row.action == "scope_revoke"

    def test_succeed_does_not_commit(self):
        db = MagicMock()
        actor = MagicMock()
        actor.id = uuid.uuid4()
        with admin_action_attempt(
            db,
            actor=actor,
            scope_used=SCOPES_REVOKE,
            action="scope_revoke",
            target_type="user",
            target_id=str(uuid.uuid4()),
        ) as attempt:
            attempt.succeed(payload={"scope": "admin.scopes.grant"})
        db.commit.assert_not_called()
        db.add.assert_called()
