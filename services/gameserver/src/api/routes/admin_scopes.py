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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth.admin_scopes import (
    ALL_SCOPES,
    META_SCOPES,
    SCOPES_GRANT,
    SCOPES_REVOKE,
)
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
    duplicate under the unique partial index.  Lock the target user row so
    concurrent grant/revoke cannot race the flat ``is_admin`` flag.
    """
    if scope not in ALL_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}",
        )

    # Serialize grant/revoke against this user (flat is_admin sync).
    locked = (
        db.query(User)
        .filter(User.id == target.id)
        .with_for_update()
        .one()
    )

    existing = (
        db.query(AdminScopeGrant)
        .filter(
            AdminScopeGrant.user_id == locked.id,
            AdminScopeGrant.scope == scope,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if existing:
        _log_action(
            db,
            actor=actor,
            scope_used=SCOPES_GRANT,
            action="scope_grant_noop",
            target_user_id=locked.id,
            payload={"scope": scope, "already_active": True},
            result="success",
        )
        if not locked.is_admin:
            locked.is_admin = True
            target.is_admin = True
        return existing

    # Savepoint: concurrent duplicate insert hits unique partial index —
    # recover as idempotent success (mack HARD #3).
    try:
        with db.begin_nested():
            row = AdminScopeGrant(
                id=uuid.uuid4(),
                user_id=locked.id,
                scope=scope,
                granted_by=actor.id,
            )
            db.add(row)
            locked.is_admin = True
            target.is_admin = True
            _log_action(
                db,
                actor=actor,
                scope_used=SCOPES_GRANT,
                action="scope_grant",
                target_user_id=locked.id,
                payload={"scope": scope},
                result="success",
            )
            db.flush()
            return row
    except IntegrityError:
        raced = (
            db.query(AdminScopeGrant)
            .filter(
                AdminScopeGrant.user_id == locked.id,
                AdminScopeGrant.scope == scope,
                AdminScopeGrant.revoked_at.is_(None),
            )
            .first()
        )
        if raced is None:
            raise
        locked.is_admin = True
        target.is_admin = True
        _log_action(
            db,
            actor=actor,
            scope_used=SCOPES_GRANT,
            action="scope_grant_noop",
            target_user_id=locked.id,
            payload={"scope": scope, "already_active": True, "raced": True},
            result="success",
        )
        return raced


def revoke_scope_from_user(
    db: Session,
    *,
    actor: User,
    target: User,
    scope: str,
) -> int:
    """Bulk-revoke all active rows for (user, scope). Returns rows touched.

    Cipher: NO ``.first()`` / LIMIT — revoke every active match.
    Mack: flush before remaining-count (autoflush=False sessions otherwise
    still see the just-revoked row → is_admin never clears).
    """
    if scope not in ALL_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope: {scope}",
        )

    locked = (
        db.query(User)
        .filter(User.id == target.id)
        .with_for_update()
        .one()
    )

    now = datetime.now(timezone.utc)
    rows = (
        db.query(AdminScopeGrant)
        .filter(
            AdminScopeGrant.user_id == locked.id,
            AdminScopeGrant.scope == scope,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .with_for_update()
        .all()
    )

    # Cipher HIGH: refuse stripping the last system-wide holder of a meta
    # scope (grant / revoke / audit.view) — otherwise one revoke-holder can
    # orphan Phase-E review + monopolize scope management.
    if rows and scope in META_SCOPES:
        other_holders = (
            db.query(AdminScopeGrant.user_id)
            .filter(
                AdminScopeGrant.scope == scope,
                AdminScopeGrant.revoked_at.is_(None),
                AdminScopeGrant.user_id != locked.id,
            )
            .distinct()
            .count()
        )
        if other_holders == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot revoke last system-wide holder of {scope}"
                ),
            )

    for row in rows:
        row.revoked_at = now
        row.revoked_by = actor.id

    # CRITICAL (mack + hub-cipher): autoflush=False — must flush before
    # counting remaining active grants, else the just-revoked rows still
    # count as active and flat is_admin never clears on last-scope revoke.
    db.flush()

    remaining = (
        db.query(AdminScopeGrant.id)
        .filter(
            AdminScopeGrant.user_id == locked.id,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .count()
    )
    if remaining == 0:
        locked.is_admin = False
        target.is_admin = False

    _log_action(
        db,
        actor=actor,
        scope_used=SCOPES_REVOKE,
        action="scope_revoke" if rows else "scope_revoke_noop",
        target_user_id=locked.id,
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
