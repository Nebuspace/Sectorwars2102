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
    SCOPE_DESCRIPTIONS,
    SCOPES_GRANT,
    SCOPES_REVOKE,
)
from src.auth.dependencies import require_scope
from src.core.database import get_db
from src.models.admin_scope_grant import AdminScopeGrant
from src.models.user import User
from src.services.admin_action_attempt import admin_action_attempt

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


class ScopeCatalogItem(BaseModel):
    scope: str
    description: str


class ScopeHolderOut(BaseModel):
    user_id: UUID
    username: Optional[str] = None
    is_admin: bool
    scopes: List[ActiveGrantOut]


class ScopeMutationOutcome(BaseModel):
    """Internal: action name + payload for E-5 attempt.succeed (no double-log)."""

    action: str
    payload: Dict[str, Any]


def grant_scope_to_user(
    db: Session,
    *,
    actor: User,
    target: User,
    scope: str,
) -> ScopeMutationOutcome:
    """Insert an active grant if missing; sync flat ``is_admin=True``.

    Cipher (Phase C preview): check existing-active before insert — never
    duplicate under the unique partial index.  Lock the target user row so
    concurrent grant/revoke cannot race the flat ``is_admin`` flag.

    Does **not** write AdminActionLog — caller uses ``admin_action_attempt``.
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
        if not locked.is_admin:
            locked.is_admin = True
            target.is_admin = True
        return ScopeMutationOutcome(
            action="scope_grant_noop",
            payload={"scope": scope, "already_active": True},
        )

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
            db.flush()
            return ScopeMutationOutcome(
                action="scope_grant",
                payload={"scope": scope},
            )
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
        return ScopeMutationOutcome(
            action="scope_grant_noop",
            payload={"scope": scope, "already_active": True, "raced": True},
        )


def revoke_scope_from_user(
    db: Session,
    *,
    actor: User,
    target: User,
    scope: str,
) -> ScopeMutationOutcome:
    """Bulk-revoke all active rows for (user, scope).

    Cipher: NO ``.first()`` / LIMIT — revoke every active match.
    Mack: flush before remaining-count (autoflush=False sessions otherwise
    still see the just-revoked row → is_admin never clears).

    Does **not** write AdminActionLog — caller uses ``admin_action_attempt``.
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

    return ScopeMutationOutcome(
        action="scope_revoke" if rows else "scope_revoke_noop",
        payload={"scope": scope, "rows_revoked": len(rows)},
    )



@router.get("/catalog", response_model=List[ScopeCatalogItem])
async def list_scope_catalog(
    _: User = Depends(require_scope(SCOPES_GRANT)),
):
    """27-scope catalog (grant holders can see what is grantable)."""
    return [
        ScopeCatalogItem(scope=scope, description=SCOPE_DESCRIPTIONS[scope])
        for scope in sorted(ALL_SCOPES)
    ]


@router.get("/holders", response_model=List[ScopeHolderOut])
async def list_scope_holders(
    _: User = Depends(require_scope(SCOPES_GRANT)),
    db: Session = Depends(get_db),
):
    """List every user with at least one active AdminScopeGrant.

    Gated on ``admin.scopes.grant`` (not ``admin.players.view``): this is
    meta-admin capability — who holds which scopes is scope-management
    intelligence, not routine player lookup.
    """
    rows = (
        db.query(
            User.id,
            User.username,
            User._is_admin,
            AdminScopeGrant.scope,
            AdminScopeGrant.granted_at,
            AdminScopeGrant.granted_by,
        )
        .join(AdminScopeGrant, AdminScopeGrant.user_id == User.id)
        .filter(
            AdminScopeGrant.revoked_at.is_(None),
            User.deleted == False,
        )
        .order_by(User.username, AdminScopeGrant.scope)
        .all()
    )
    holders: dict[UUID, ScopeHolderOut] = {}
    for user_id, username, is_admin, scope, granted_at, granted_by in rows:
        if user_id not in holders:
            holders[user_id] = ScopeHolderOut(
                user_id=user_id,
                username=username,
                is_admin=bool(is_admin),
                scopes=[],
            )
        holders[user_id].scopes.append(
            ActiveGrantOut(
                scope=scope,
                granted_at=granted_at,
                granted_by=granted_by,
            )
        )
    return list(holders.values())


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
    with admin_action_attempt(
        db,
        actor=actor,
        scope_used=SCOPES_GRANT,
        action="scope_grant",
        target_type="user",
        target_id=str(body.user_id),
        payload={"scope": body.scope},
    ) as attempt:
        target = (
            db.query(User)
            .filter(User.id == body.user_id, User.deleted == False)
            .first()
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            outcome = grant_scope_to_user(
                db, actor=actor, target=target, scope=body.scope
            )
            attempt.succeed(action=outcome.action, payload=outcome.payload)
            db.commit()
            db.refresh(target)
        except HTTPException:
            raise
        except Exception as exc:
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
    with admin_action_attempt(
        db,
        actor=actor,
        scope_used=SCOPES_REVOKE,
        action="scope_revoke",
        target_type="user",
        target_id=str(body.user_id),
        payload={"scope": body.scope},
    ) as attempt:
        target = (
            db.query(User)
            .filter(User.id == body.user_id, User.deleted == False)
            .first()
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            outcome = revoke_scope_from_user(
                db, actor=actor, target=target, scope=body.scope
            )
            attempt.succeed(action=outcome.action, payload=outcome.payload)
            db.commit()
            db.refresh(target)
        except HTTPException:
            raise
        except Exception as exc:
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
