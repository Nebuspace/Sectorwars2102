"""Admin scope grant/revoke API — RBAC Phase B (ADR-0058).

Sole path to admin-hood after Max's 2026-07-17 ruling: RETIRE
``POST /users/admin``; minting capability = inserting AdminScopeGrant rows
gated on ``admin.scopes.grant`` / ``admin.scopes.revoke``.  Every mutation
self-logs to AdminActionLog (acting admin cannot suppress the write).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth.admin_scopes import ALL_SCOPES, SCOPES_GRANT, SCOPES_REVOKE
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.admin_action_log import AdminActionLog
from src.models.admin_scope_grant import AdminScopeGrant
from src.models.user import User

router = APIRouter(prefix="/admin/scopes", tags=["admin-scopes"])


class ScopeMutationRequest(BaseModel):
    user_id: UUID
    scope: str = Field(..., min_length=1, max_length=120)


class ScopeMutationResponse(BaseModel):
    user_id: UUID
    scope: str
    active: bool
    is_admin: bool


class ActiveGrantOut(BaseModel):
    scope: str
    granted_at: Optional[datetime] = None
    granted_by: Optional[UUID] = None


class UserScopesOut(BaseModel):
    user_id: UUID
    is_admin: bool
    scopes: List[ActiveGrantOut]


def _log_action(
    db: Session,
    *,
    actor: User,
    scope_used: str,
    action: str,
    target_user_id: UUID,
    payload: Dict[str, Any],
    result: str,
    failure_reason: Optional[str] = None,
) -> None:
    db.add(
        AdminActionLog(
            id=uuid.uuid4(),
            admin_user_id=actor.id,
            scope_used=scope_used,
            action=action,
            target_type="user",
            target_id=str(target_user_id),
            payload_snapshot=payload,
            result=result,
            failure_reason=failure_reason,
        )
    )


def grant_scope_to_user(
    db: Session,
    *,
    actor: User,
    target: User,
    scope: str,
) -> AdminScopeGrant:
    """Insert an active grant if missing; sync flat ``is_admin=True``.

    Cipher (Phase C preview): check existing-active before insert — never
    duplicate under the unique partial index.
    """
    if scope not in ALL_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}",
        )

    existing = (
        db.query(AdminScopeGrant)
        .filter(
            AdminScopeGrant.user_id == target.id,
            AdminScopeGrant.scope == scope,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .first()
    )
    if existing:
        _log_action(
            db,
            actor=actor,
            scope_used=SCOPES_GRANT,
            action="scope_grant_noop",
            target_user_id=target.id,
            payload={"scope": scope, "already_active": True},
            result="success",
        )
        if not target.is_admin:
            target.is_admin = True
        return existing

    row = AdminScopeGrant(
        id=uuid.uuid4(),
        user_id=target.id,
        scope=scope,
        granted_by=actor.id,
    )
    db.add(row)
    # Flat column stays authoritative through B/C — sync on mint.
    target.is_admin = True
    _log_action(
        db,
        actor=actor,
        scope_used=SCOPES_GRANT,
        action="scope_grant",
        target_user_id=target.id,
        payload={"scope": scope},
        result="success",
    )
    return row


def revoke_scope_from_user(
    db: Session,
    *,
    actor: User,
    target: User,
    scope: str,
) -> int:
    """Bulk-revoke all active rows for (user, scope). Returns rows touched.

    Cipher: NO ``.first()`` / LIMIT — revoke every active match.
    """
    if scope not in ALL_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}",
        )

    now = datetime.now(timezone.utc)
    q = db.query(AdminScopeGrant).filter(
        AdminScopeGrant.user_id == target.id,
        AdminScopeGrant.scope == scope,
        AdminScopeGrant.revoked_at.is_(None),
    )
    rows = q.all()
    for row in rows:
        row.revoked_at = now
        row.revoked_by = actor.id

    remaining = (
        db.query(AdminScopeGrant.id)
        .filter(
            AdminScopeGrant.user_id == target.id,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .count()
    )
    if remaining == 0:
        target.is_admin = False

    _log_action(
        db,
        actor=actor,
        scope_used=SCOPES_REVOKE,
        action="scope_revoke",
        target_user_id=target.id,
        payload={"scope": scope, "rows_revoked": len(rows)},
        result="success",
    )
    return len(rows)


@router.get("/catalog", response_model=List[str])
async def list_scope_catalog(
    _: User = Depends(require_scope(SCOPES_GRANT)),
):
    """Frozen 19-scope catalog (grant holders can see what is grantable)."""
    return sorted(ALL_SCOPES)


@router.get("/users/{user_id}", response_model=UserScopesOut)
async def list_user_scopes(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_scope(SCOPES_GRANT)),
):
    target = db.query(User).filter(User.id == user_id, User.deleted == False).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    grants = (
        db.query(AdminScopeGrant)
        .filter(
            AdminScopeGrant.user_id == user_id,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .all()
    )
    return UserScopesOut(
        user_id=user_id,
        is_admin=bool(target.is_admin),
        scopes=[
            ActiveGrantOut(
                scope=g.scope,
                granted_at=g.granted_at,
                granted_by=g.granted_by,
            )
            for g in grants
        ],
    )


@router.post("/grant", response_model=ScopeMutationResponse)
async def grant_scope(
    body: ScopeMutationRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_scope(SCOPES_GRANT)),
):
    target = db.query(User).filter(User.id == body.user_id, User.deleted == False).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        grant_scope_to_user(db, actor=actor, target=target, scope=body.scope)
        db.commit()
        db.refresh(target)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Grant failed") from exc

    return ScopeMutationResponse(
        user_id=target.id,
        scope=body.scope,
        active=True,
        is_admin=bool(target.is_admin),
    )


@router.post("/revoke", response_model=ScopeMutationResponse)
async def revoke_scope(
    body: ScopeMutationRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_scope(SCOPES_REVOKE)),
):
    target = db.query(User).filter(User.id == body.user_id, User.deleted == False).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        revoke_scope_from_user(db, actor=actor, target=target, scope=body.scope)
        db.commit()
        db.refresh(target)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Revoke failed") from exc

    still = (
        db.query(AdminScopeGrant.id)
        .filter(
            AdminScopeGrant.user_id == target.id,
            AdminScopeGrant.scope == body.scope,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .first()
    )
    return ScopeMutationResponse(
        user_id=target.id,
        scope=body.scope,
        active=still is not None,
        is_admin=bool(target.is_admin),
    )
