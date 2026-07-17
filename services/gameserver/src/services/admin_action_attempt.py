"""Admin mutation attempt audit — RBAC Phase E-5 (ADR-0058).

Context manager that logs success / blocked / failed outcomes for admin
mutations without scattering ad-hoc ``log_admin_action`` calls.

Commit policy (translation / C2b lesson):
- **success** — caller commits the mutation + log row in one txn (same as C1/C2).
- **blocked/failed with no surviving mutation** — helper rolls back the session,
  writes the attempt log, and **commits the log alone** so a 409/400 rejection
  still leaves a durable trail.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any, Dict, Literal, Optional, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.models.user import User
from src.services.admin_action_log_service import log_admin_action

AdminAttemptResult = Literal["success", "blocked", "failed"]

_SECRETISH = re.compile(
    r"(?i)(password|secret|token|authorization|api[_-]?key)\s*[:=]\s*\S+"
)


def sanitize_failure_reason(reason: Any, *, max_len: int = 500) -> str:
    """Strip stacks/secrets from HTTP detail before persisting."""
    if reason is None:
        text = "unknown"
    elif isinstance(reason, (list, dict)):
        text = str(reason)
    else:
        text = str(reason)
    text = _SECRETISH.sub(r"\1=[redacted]", text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text or "unknown"


def _result_for_http(status_code: int) -> AdminAttemptResult:
    # Authz already passed require_scope — 403 here is business deny, still blocked.
    if status_code in (400, 403, 404, 409, 422):
        return "blocked"
    return "failed"


class admin_action_attempt:
    """``with admin_action_attempt(...) as attempt:`` — call ``succeed()`` on OK.

    Uncaught ``HTTPException`` / other Exception → ``fail()`` with own commit.
    """

    def __init__(
        self,
        db: Session,
        *,
        actor: User,
        scope_used: str,
        action: str,
        target_type: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.db = db
        self.actor = actor
        self.scope_used = scope_used
        self.action = action
        self.target_type = target_type
        self.target_id = str(target_id)
        self.payload = payload
        self._finalized = False

    def __enter__(self) -> "admin_action_attempt":
        return self

    def succeed(
        self,
        *,
        payload: Optional[Dict[str, Any]] = None,
        action: Optional[str] = None,
    ) -> None:
        """Append success log (caller must ``db.commit()`` with the mutation)."""
        if self._finalized:
            return
        log_admin_action(
            self.db,
            actor=self.actor,
            scope_used=self.scope_used,
            action=action or self.action,
            target_type=self.target_type,
            target_id=self.target_id,
            payload=payload if payload is not None else self.payload,
            result="success",
        )
        self._finalized = True

    def fail(
        self,
        *,
        result: AdminAttemptResult,
        reason: Any,
        commit: bool = True,
    ) -> None:
        """Append blocked/failed log; optionally commit the log alone."""
        if self._finalized:
            return
        if result == "success":
            raise ValueError("fail() requires blocked|failed")
        log_admin_action(
            self.db,
            actor=self.actor,
            scope_used=self.scope_used,
            action=self.action,
            target_type=self.target_type,
            target_id=self.target_id,
            payload=self.payload,
            result=result,
            failure_reason=sanitize_failure_reason(reason),
        )
        if commit:
            self.db.commit()
        self._finalized = True

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        if self._finalized or exc is None:
            return False

        # Drop any uncommitted mutation side effects before writing the trail.
        try:
            self.db.rollback()
        except Exception:
            pass

        if isinstance(exc, HTTPException):
            self.fail(
                result=_result_for_http(exc.status_code),
                reason=exc.detail,
                commit=True,
            )
            return False

        self.fail(
            result="failed",
            reason=getattr(exc, "args", None) and exc.args[0] or type(exc).__name__,
            commit=True,
        )
        return False


# E-5 first-cut wrapped surfaces (HIGH_IMPACT). Deferred routes listed in
# tests/unit/test_rbac_phase_e5_attempts.py — do not silently expand coverage.
E5_WRAPPED_ROUTES: frozenset[str] = frozenset(
    {
        "POST /admin/scopes/grant",
        "POST /admin/scopes/revoke",
        "POST /admin/contracts/{contract_id}/resolve-dispute",
    }
)
