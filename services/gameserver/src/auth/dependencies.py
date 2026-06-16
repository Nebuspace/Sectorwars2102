import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError as JWTError
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.auth.jwt import decode_token
from src.core.database import get_async_session, get_db
from src.models.user import User
from src.models.player import Player

logger = logging.getLogger(__name__)

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login/direct")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency to get the current authenticated user from the token.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.id == user_id, User.deleted == False).first()
    if user is None or not user.is_active:
        raise credentials_exception
        
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency to ensure the user is active.
    """
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency to ensure the user is an admin.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for admin access"
        )
    return current_user

# Aliases for get_current_admin_user to match naming convention in admin routes
get_current_admin = get_current_admin_user
require_admin = get_current_admin_user
require_auth = get_current_user  # Alias for authentication requirement


async def get_current_admin_from_header_or_query(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Admin auth that accepts the JWT from either the Authorization header
    or a ``?token=`` query parameter. Required for browser EventSource
    (SSE) clients, which cannot set custom headers.

    Mirrors :func:`get_current_admin_user` semantics: 401 on missing/invalid
    token, 403 on a valid non-admin token.
    """
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    user = db.query(User).filter(User.id == user_id, User.deleted == False).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for admin access")
    return user

# Allow both OPTIONS and other methods
# This is needed for CORS preflight requests in GitHub Codespaces
def admin_or_options(
    _: User = Depends(get_current_admin_user),
) -> User:
    """
    Wrapper for get_current_admin_user that allows OPTIONS requests.
    """
    return _

async def get_current_player(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Player:
    """
    Dependency to get the current player associated with the authenticated user.
    """
    player = db.query(Player).filter(Player.user_id == current_user.id).first()
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player account not found"
        )

    _enforce_subscription_expiry(db, current_user, player)
    return player


def _enforce_subscription_expiry(db: Session, user: User, player: Player) -> None:
    """Per-request galactic-citizenship lapse check (ARCHITECTURE/auth.md).

    Citizenship is granted by a PayPal webhook but, without this, was never
    revoked when ``subscription_expires_at`` passed. We drop it lazily on the
    first request after expiry. This writes exactly once — on the expired→true
    transition — because the flag is then ``False`` so the guard no longer holds;
    a later renewal webhook restores both the flag and the expiry.
    """
    if not player.is_galactic_citizen or user.subscription_expires_at is None:
        return

    expires = user.subscription_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires >= datetime.now(timezone.utc):
        return

    player.is_galactic_citizen = False
    user.subscription_status = "expired"
    db.commit()
    logger.info("Dropped lapsed galactic citizenship for player %s", player.id)


async def get_current_user_from_token(
    token: str, 
    db: Session
) -> User:
    """
    Function to get the current authenticated user from a token string.
    Used for WebSocket authentication where we can't use FastAPI dependencies.
    """
    if not token:
        return None
    
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
        
    user = db.query(User).filter(User.id == user_id, User.deleted == False).first()
    if user is None or not user.is_active:
        return None
        
    return user


async def validate_websocket_token(token: str, db: AsyncSession) -> Player:
    """
    Validate WebSocket authentication token and return Player
    Used for WebSocket connections where standard FastAPI dependencies don't work
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required"
        )
    
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    
    # Get user with async query
    stmt = select(User).where(User.id == user_id, User.deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    # Get player associated with user
    stmt = select(Player).where(Player.user_id == user.id)
    result = await db.execute(stmt)
    player = result.scalar_one_or_none()
    
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player account not found"
        )
    
    return player


async def validate_ai_access(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_async_session)) -> str:
    """
    Validate access to AI features and return player_id
    Used by enhanced AI routes
    """
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )
    
    # Get player ID for user
    stmt = select(Player.id).where(Player.user_id == user_id)
    result = await db.execute(stmt)
    player_id = result.scalar_one_or_none()
    
    if player_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player account not found"
        )
    
    return str(player_id)