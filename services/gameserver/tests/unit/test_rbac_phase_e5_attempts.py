"""RBAC Phase E-5 — admin_action_attempt helper + first-cut wrap set.

DB-free source asserts + in-memory smoke for blocked-attempt own-commit
and hub-cipher REVISE fixes (guarded fail-commit · helper-owned success
commit · Bearer sanitize).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.auth.admin_scopes import HIGH_IMPACT_SCOPES, SCOPES_REVOKE
from src.models.admin_action_log import AdminActionLog
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

    def test_redacts_bearer_scheme_prefixed_token(self):
        raw = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb"
        out = sanitize_failure_reason(raw)
        assert "eyJhbGci" not in out
        assert "Bearer" not in out or "[redacted]" in out
        assert "[redacted]" in out

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
                "PATCH /admin/players/{player_id}",
                "POST /admin/players/create-from-user",
                "POST /admin/players/create-bulk",
                "POST /admin/ships",
                "PUT /admin/ships/{ship_id}",
                "DELETE /admin/ships/{ship_id}",
                "POST /admin/ships/{ship_id}/teleport",
                "POST /admin/ships/create",
                "POST /admin/ships/{ship_id}/emergency",
            }
        )
        assert SCOPES_REVOKE in HIGH_IMPACT_SCOPES

    def test_grant_revoke_helper_owns_commit_no_route_commit_after_succeed(self):
        grant = _SCOPES_SRC.split('@router.post("/grant"', 1)[1].split(
            '@router.post("/revoke"', 1
        )[0]
        revoke = _SCOPES_SRC.split('@router.post("/revoke"', 1)[1]
        assert "attempt.succeed" in grant
        assert "attempt.succeed" in revoke
        # Helper owns commit — routes must not commit again after succeed.
        assert "attempt.succeed" in grant
        after_succeed_grant = grant.split("attempt.succeed", 1)[1].split(
            "return ScopeMutationResponse", 1
        )[0]
        assert "db.commit()" not in after_succeed_grant
        after_succeed_revoke = revoke.split("attempt.succeed", 1)[1].split(
            "still =", 1
        )[0]
        assert "db.commit()" not in after_succeed_revoke

    def test_disputes_resolve_helper_owns_commit(self):
        block = _DISPUTES_SRC.split(
            '@router.post("/{contract_id}/resolve-dispute"', 1
        )[1]
        assert "admin_action_attempt" in block
        assert "attempt.succeed" in block
        after = block.split("attempt.succeed", 1)[1]
        assert "db.commit()" not in after
        assert "log_admin_action" not in block

    def test_helper_documents_owned_commit_and_best_effort_fail(self):
        assert "OWNS the commit" in _ATTEMPT_SRC or "owns the commit" in _ATTEMPT_SRC.lower()
        assert "best-effort" in _ATTEMPT_SRC.lower()
        assert '"blocked"' in _ATTEMPT_SRC
        assert '"failed"' in _ATTEMPT_SRC
        assert "bearer" in _ATTEMPT_SRC.lower()


class TestBlockedAttemptOwnCommit:
    def test_http_409_logs_blocked_and_commits(self):
        db = MagicMock()
        actor = MagicMock()
        actor.id = uuid.uuid4()

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

    def test_fail_commit_error_preserves_original_http_exception(self):
        """MEDIUM 1: log-commit failure must not replace the business error."""
        db = MagicMock()
        actor = MagicMock()
        actor.id = uuid.uuid4()
        db.commit.side_effect = RuntimeError("deadlock detected")

        with pytest.raises(HTTPException) as ei:
            with admin_action_attempt(
                db,
                actor=actor,
                scope_used=SCOPES_REVOKE,
                action="scope_revoke",
                target_type="user",
                target_id=str(uuid.uuid4()),
            ):
                raise HTTPException(status_code=409, detail="last holder")

        assert ei.value.status_code == 409
        assert ei.value.detail == "last holder"

    def test_succeed_commits_and_finalizes(self):
        """MEDIUM 2: helper owns commit; finalize only after persist."""
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
        db.commit.assert_called()
        db.add.assert_called()
        row = db.add.call_args[0][0]
        assert isinstance(row, AdminActionLog)
        assert row.result == "success"

    def test_succeed_commit_failure_writes_failed_row_then_reraises(self):
        db = MagicMock()
        actor = MagicMock()
        actor.id = uuid.uuid4()
        # First commit (success path) fails; second commit (failure log) ok.
        db.commit.side_effect = [RuntimeError("commit boom"), None]

        with pytest.raises(RuntimeError, match="commit boom"):
            with admin_action_attempt(
                db,
                actor=actor,
                scope_used=SCOPES_REVOKE,
                action="scope_revoke",
                target_type="user",
                target_id=str(uuid.uuid4()),
            ) as attempt:
                attempt.succeed(payload={"scope": "x"})

        assert db.add.call_count >= 2
        results = [c[0][0].result for c in db.add.call_args_list]
        assert "success" in results
        assert "failed" in results
