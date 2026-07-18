"""Shared AdminActionLog writer — RBAC Phase C (ADR-0058 A-F2).

Callers ``db.add`` via this helper in the **same session** as the mutation,
then commit once.  The acting admin cannot suppress the write without rolling
back the mutation itself.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.models.admin_action_log import AdminActionLog
from src.models.user import User


def log_admin_action(
    db: Session,
    *,
    actor: User,
    scope_used: str,
    action: str,
    target_type: str,
    target_id: str,
    payload: Optional[Dict[str, Any]] = None,
    result: str = "success",
    failure_reason: Optional[str] = None,
) -> None:
    """Append one AdminActionLog row (flush/commit is the caller's job)."""
    db.add(
        AdminActionLog(
            id=uuid.uuid4(),
            admin_user_id=actor.id,
            scope_used=scope_used,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            payload_snapshot=payload,
            result=result,
            failure_reason=failure_reason,
        )
    )
