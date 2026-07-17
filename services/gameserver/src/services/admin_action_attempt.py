"""Admin mutation attempt audit — RBAC Phase E-5 (ADR-0058).

Context manager that logs success / blocked / failed outcomes for admin
mutations without scattering ad-hoc ``log_admin_action`` calls.

Commit policy (hub-cipher E-5 re-gate):
- **The helper OWNS the commit boundary** for wrapped routes.
  ``succeed()`` adds the success log and commits mutation+log together.
  Never finalize-to-success before that commit persists.
- **blocked/failed** — helper rolls back the session, writes the attempt log,
  and commits the log alone (best-effort). If the log-commit itself fails,
  the ORIGINAL exception is still re-raised (log is best-effort).
"""

from __future__ import annotations

import logging
import re
from types import TracebackType
from typing import Any, Dict, Literal, Optional, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.models.user import User
from src.services.admin_action_log_service import log_admin_action

logger = logging.getLogger(__name__)

AdminAttemptResult = Literal["success", "blocked", "failed"]

# Redact key + everything after it (incl. "Bearer <jwt>" scheme-prefixed tokens).
_SECRETISH = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)\b"
    r"(?:\s*[:=]\s*|\s+)(?:bearer\s+)?\S+"
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

    ``succeed()`` commits (helper-owned boundary). Uncaught exceptions →
    ``fail()`` with best-effort log commit; original exception always propagates.
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
        """Log success and commit mutation+log (helper owns the commit).

        If commit fails: best-effort failure audit row, then re-raise so the
        caller/__exit__ never sees a silent zero-row success finalize.
        """
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
        try:
            self.db.commit()
        except Exception as commit_exc:
            # Success log never persisted — record a failure trail, then re-raise.
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                log_admin_action(
                    self.db,
                    actor=self.actor,
                    scope_used=self.scope_used,
                    action=action or self.action,
                    target_type=self.target_type,
                    target_id=self.target_id,
                    payload=payload if payload is not None else self.payload,
                    result="failed",
                    failure_reason=sanitize_failure_reason(commit_exc),
                )
                self.db.commit()
            except Exception as log_exc:
                logger.warning(
                    "admin_action_attempt: success-commit failed and failure-log "
                    "also failed: %s / %s",
                    commit_exc,
                    log_exc,
                )
                try:
                    self.db.rollback()
                except Exception:
                    pass
            self._finalized = True
            raise commit_exc
        self._finalized = True

    def fail(
        self,
        *,
        result: AdminAttemptResult,
        reason: Any,
        commit: bool = True,
    ) -> None:
        """Append blocked/failed log; best-effort commit the log alone.

        Commit failures are swallowed so the ORIGINAL exception can propagate
        from ``__exit__`` (log is best-effort, never replaces the business error).
        """
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
            try:
                self.db.commit()
            except Exception as log_exc:
                logger.warning(
                    "admin_action_attempt: fail() log-commit failed (best-effort): %s",
                    log_exc,
                )
                try:
                    self.db.rollback()
                except Exception:
                    pass
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
# tests/unit/test_rbac_phase_e5_attempts.py — do not silently expand coverage
# until hub-cipher RE-GATE on this helper PASSes.
E5_WRAPPED_ROUTES: frozenset[str] = frozenset(
    {
        "POST /admin/scopes/grant",
        "POST /admin/scopes/revoke",
        "POST /admin/contracts/{contract_id}/resolve-dispute",
        # Wave-2 money-path (PLAYERS_ADJUST_CREDITS) — after real-session boundary test
        "PATCH /admin/players/{player_id}",
        "POST /admin/players/create-from-user",
        "POST /admin/players/create-bulk",
        # Wave-2 ships (SHIPS_MANAGE) — sync routes; drones deferred (AsyncSession)
        "POST /admin/ships",
        "PUT /admin/ships/{ship_id}",
        "DELETE /admin/ships/{ship_id}",
        "POST /admin/ships/{ship_id}/teleport",
        "POST /admin/ships/create",
        "POST /admin/ships/{ship_id}/emergency",
        # Wave-2 galaxy (GALAXY_MANAGE) — admin.py sync mutators
        "POST /admin/warp-tunnels/create",
        "DELETE /admin/galaxy/clear",
        "POST /admin/galaxy/fix-statistics",
        "PATCH /admin/ports/{station_id}",
        "POST /admin/game-events",
        "PATCH /admin/game-events/{event_id}",
        "POST /admin/game-events/{event_id}/activate",
        "POST /admin/game-events/{event_id}/deactivate",
        "DELETE /admin/game-events/{event_id}",
    }
)
