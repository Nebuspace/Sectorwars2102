"""Admin review queue for multi-account detection clusters (ADR-0056 /
WO-PADMIN-multiacct-review).

Exposes ``MultiAccountCluster`` and ``MultiAccountFlag`` rows (written by the
future ``MultiAccountDetectionService`` sweep) over REST so an admin can list
pending clusters, inspect evidence, and record a ruling (confirmed /
overridden / escalated).

Auth: ``require_scope(MULTI_ACCOUNT_REVIEW)`` is resolved BEFORE ``get_db`` on
every route signature — an unauthenticated or scopeless caller is rejected
before any DB access occurs.  See tests/unit/test_admin_multi_account.py for
the 401/403 never-mutate proof.

[Honest gap] The ``MultiAccountDetectionService`` and its hourly scheduler
sweep (P7-admin-multiacct-service-sweep) have not shipped yet — the
``multi_account_clusters`` / ``multi_account_flags`` tables are schema-only at
this point.  The review queue will be empty in a freshly-seeded game until
that sweep runs.  This route surfaces whatever the DB holds, records admin
decisions, and documents the dependency clearly rather than inventing
detection heuristics.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.admin_scopes import MULTI_ACCOUNT_REVIEW
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.multi_account import (
    MultiAccountAdminDecision,
    MultiAccountCluster,
    MultiAccountFlag,
)
from src.models.user import User
from src.services.admin_action_log_service import log_admin_action

router = APIRouter(prefix="/admin/multi-account", tags=["admin-multi-account"])

# Rulings an admin may record.  PENDING is the initial state, not a valid
# admin action.
_ALLOWED_DECISIONS = {
    MultiAccountAdminDecision.CONFIRMED,
    MultiAccountAdminDecision.OVERRIDDEN,
    MultiAccountAdminDecision.ESCALATED,
}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_flag(f: MultiAccountFlag) -> Dict[str, Any]:
    return {
        "id": str(f.id),
        "player_id": str(f.player_id),
        "signal": f.signal,
        "severity": f.severity.value if f.severity else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _serialize_cluster(
    c: MultiAccountCluster, *, include_flags: bool = False
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": str(c.id),
        "signal_summary": c.signal_summary,
        "severity": c.severity.value if c.severity else None,
        "all_paid_subscribers": c.all_paid_subscribers,
        "admin_decision": c.admin_decision.value if c.admin_decision else None,
        "admin_decision_reason": c.admin_decision_reason,
        "admin_decision_at": (
            c.admin_decision_at.isoformat() if c.admin_decision_at else None
        ),
        "admin_decision_by": (
            str(c.admin_decision_by) if c.admin_decision_by else None
        ),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "member_count": len(c.flags) if c.flags is not None else 0,
    }
    if include_flags:
        row["flags"] = [_serialize_flag(f) for f in (c.flags or [])]
    return row


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}",
        ) from None


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class ClusterDecisionRequest(BaseModel):
    """Body for POST /clusters/{id}/decide.

    ``decision`` must be one of confirmed | overridden | escalated.
    Setting pending is rejected — that is the initial state, not a ruling.
    """

    decision: str
    reason: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/clusters", response_model=List[Dict[str, Any]])
def list_clusters(
    decision: Optional[str] = Query(
        None,
        description=(
            "Filter by admin_decision value (pending | confirmed | overridden | "
            "escalated).  Omit to return pending clusters only."
        ),
    ),
    admin: User = Depends(require_scope(MULTI_ACCOUNT_REVIEW)),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List multi-account clusters.  Defaults to pending-only; supply
    ``?decision=<value>`` to filter by a specific decision state."""
    q = db.query(MultiAccountCluster)
    if decision is not None:
        try:
            decision_enum = MultiAccountAdminDecision(decision)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown decision value '{decision}'; valid: "
                    "pending, confirmed, overridden, escalated"
                ),
            )
        q = q.filter(MultiAccountCluster.admin_decision == decision_enum)
    else:
        q = q.filter(
            MultiAccountCluster.admin_decision == MultiAccountAdminDecision.PENDING
        )
    clusters = q.order_by(MultiAccountCluster.created_at.desc()).all()
    return [_serialize_cluster(c) for c in clusters]


@router.get("/clusters/{cluster_id}", response_model=Dict[str, Any])
def get_cluster(
    cluster_id: str,
    admin: User = Depends(require_scope(MULTI_ACCOUNT_REVIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return a single cluster with its full flag list (evidence panel)."""
    cid = _parse_uuid(cluster_id, "cluster_id")
    c = (
        db.query(MultiAccountCluster)
        .filter(MultiAccountCluster.id == cid)
        .first()
    )
    if c is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found"
        )
    return _serialize_cluster(c, include_flags=True)


@router.post("/clusters/{cluster_id}/decide", response_model=Dict[str, Any])
def decide_cluster(
    cluster_id: str,
    body: ClusterDecisionRequest,
    admin: User = Depends(require_scope(MULTI_ACCOUNT_REVIEW)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Record an admin ruling on a cluster (confirmed / overridden / escalated).

    Auth: ``require_scope(MULTI_ACCOUNT_REVIEW)`` resolves BEFORE ``get_db`` in
    the signature — a 401/403 rejection never reaches the DB.  The ``pending``
    decision value is
    explicitly rejected (initial state, not a valid ruling).
    """
    try:
        decision_enum = MultiAccountAdminDecision(body.decision)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown decision '{body.decision}'; valid: "
                "confirmed, overridden, escalated"
            ),
        )
    if decision_enum not in _ALLOWED_DECISIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot set decision to 'pending' — that is the initial state, "
                "not a valid admin ruling"
            ),
        )

    cid = _parse_uuid(cluster_id, "cluster_id")
    c = (
        db.query(MultiAccountCluster)
        .filter(MultiAccountCluster.id == cid)
        .first()
    )
    if c is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found"
        )

    c.admin_decision = decision_enum
    c.admin_decision_reason = body.reason
    c.admin_decision_at = datetime.now(timezone.utc)
    c.admin_decision_by = admin.id
    log_admin_action(
        db,
        actor=admin,
        scope_used=MULTI_ACCOUNT_REVIEW,
        action="multi_account_decide",
        target_type="multi_account_cluster",
        target_id=str(cid),
        payload={
            "decision": decision_enum.value,
            "reason": body.reason,
        },
    )
    db.commit()
    db.refresh(c)
    return _serialize_cluster(c, include_flags=True)
