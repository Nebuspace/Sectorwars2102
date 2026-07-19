"""
Test routes for use in e2e testing.
These endpoints should only be accessible in test/dev environments
and require admin authentication.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from uuid import uuid4

from src.core.database import get_async_session
from src.core.config import settings
from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.models.admin_scope_grant import AdminScopeGrant
from src.core.security import get_password_hash
from src.auth.admin_scopes import META_SCOPES, SCOPES_GRANT
from src.auth.dependencies import require_scope

router = APIRouter()


class CreateAdminRequest(BaseModel):
    username: str
    password: str
    email: str


@router.get("/check-admin-exists")
async def check_admin_exists(
    username: str = Query(..., description="Username to check"),
    current_admin: User = Depends(require_scope(SCOPES_GRANT)),
    db: Session = Depends(get_async_session)
):
    """
    Check if an admin user with the given username exists.
    This endpoint is for testing purposes only. Requires scopes.grant
    (create-admin = grant-only per Max ruling; not PLAYERS_VIEW).
    """
    if not settings.TESTING and not settings.DEVELOPMENT_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available in test environments"
        )

    user = db.query(User).filter(User.username == username, User.is_admin == True).first()
    return {"exists": user is not None}


@router.post("/create-admin", status_code=status.HTTP_201_CREATED)
async def create_admin(
    request: CreateAdminRequest,
    current_admin: User = Depends(require_scope(SCOPES_GRANT)),
    db: Session = Depends(get_async_session)
):
    """
    Create an admin user for testing purposes.
    Dev/stage only. Gated on SCOPES_GRANT — minting an admin is grant-equivalent
    (PLAYERS_VIEW must never reach this surface).

    Same-txn META_SCOPES grants (hub residual on #2): flat is_admin alone would
    mint a phantom admin after the Phase-C derived flip.
    """
    if not settings.TESTING and not settings.DEVELOPMENT_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available in test environments"
        )

    # Check if user already exists
    existing_user = db.query(User).filter(User.username == request.username).first()
    if existing_user:
        return {"message": "User already exists", "user_id": str(existing_user.id)}

    # Create the user
    user = User(
        id=uuid4(),
        username=request.username,
        email=request.email,
        is_admin=True,
        is_active=True
    )
    db.add(user)
    db.flush()  # Flush to get the ID

    # Create admin credentials
    admin_creds = AdminCredentials(
        id=uuid4(),
        user_id=user.id,
        password_hash=get_password_hash(request.password)
    )
    db.add(admin_creds)

    # Grant-consistent mint (same pattern as create_default_admin).
    for scope in META_SCOPES:
        db.add(
            AdminScopeGrant(
                id=uuid4(),
                user_id=user.id,
                scope=scope,
                granted_by=current_admin.id,
            )
        )

    # Commit the transaction
    db.commit()

    return {"message": "Admin user created successfully", "user_id": str(user.id)}
