import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError as JWTError
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.auth.jwt import decode_token
from src.auth.admin_scopes import ALL_SCOPES
from src.core.database import get_async_session, get_db
from src.models.user import User
from src.models.player import Player
from src.models.admin_scope_grant import AdminScopeGrant

logger = logging.getLogger(__name__)

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login/direct")

# QUEUE-LIVENESS-SIGNAL: throttle window for the post-auth activity touch
# (see _touch_liveness_signal below) — PRESENCE_STALE_MINUTES (30, see
# services/scheduler/_common.py) is the presence sweep's staleness cutoff;
# 5 minutes gives 6 refresh opportunities inside that window (comfortable
# margin even in the worst case: a write lands right after a throttle
# reset, the player then goes idle) while keeping DB write volume to at
# most one extra UPDATE per active player per window, not per request.
_LIVENESS_TOUCH_THROTTLE = timedelta(minutes=5)


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


def user_has_active_scope(db: Session, user_id, scope: str) -> bool:
    """Return True iff ``user_id`` holds an active grant for ``scope``.

    Raises on DB failure — callers that gate auth MUST catch and fail closed
    (403), never swallow into allow.  Do NOT copy ``_touch_liveness_signal``.
    """
    row = (
        db.query(AdminScopeGrant.id)
        .filter(
            AdminScopeGrant.user_id == user_id,
            AdminScopeGrant.scope == scope,
            AdminScopeGrant.revoked_at.is_(None),
        )
        .first()
    )
    return row is not None


def require_scope(scope: str) -> Callable:
    """FastAPI dependency factory: require an active AdminScopeGrant.

    Additive alongside ``require_admin`` (Phase A2) — routes are not swept
    until Phase B.  Fail-CLOSED: any exception during the grant lookup
    becomes 403 naming the missing scope (never 200 / never 500 leak).

    403 body names the scope (ADR-0058).  Unknown catalog scopes raise at
    dependency-factory construction time so a typo cannot silently deny
    every request without a deploy-time signal.
    """
    if scope not in ALL_SCOPES:
        raise ValueError(
            f"require_scope({scope!r}): not in the canonical 19-scope catalog"
        )

    missing = f"Missing required scope: {scope}"

    async def _require_scope(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        try:
            allowed = user_has_active_scope(db, current_user.id, scope)
        except Exception:
            # Cipher #5: DB failure → 403 NEVER 200.  Log for ops; do not
            # surface internals to the client.
            logger.exception(
                "require_scope(%s) grant lookup failed for user=%s — fail-closed 403",
                scope,
                current_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=missing,
            )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=missing,
            )
        return current_user

    _require_scope.__name__ = f"require_scope[{scope}]"
    _require_scope.__require_scope__ = scope  # coverage-test hook (Phase B)
    return _require_scope


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

    Prefer :func:`require_scope_from_header_or_query` for new RBAC routes.
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


def require_scope_from_header_or_query(scope: str) -> Callable:
    """Like ``require_scope`` but accepts JWT from Authorization OR ``?token=``.

    For SSE/EventSource admin streams that cannot set custom headers.
    Fail-closed on grant-lookup errors (same Cipher #5 rule as require_scope).
    """
    if scope not in ALL_SCOPES:
        raise ValueError(
            f"require_scope_from_header_or_query({scope!r}): not in the canonical 19-scope catalog"
        )
    missing = f"Missing required scope: {scope}"

    async def _dep(
        token: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        db: Session = Depends(get_db),
    ) -> User:
        if not token and authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
            )
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
            )
        user = db.query(User).filter(User.id == user_id, User.deleted == False).first()
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User inactive or missing",
            )
        try:
            allowed = user_has_active_scope(db, user.id, scope)
        except Exception:
            logger.exception(
                "require_scope_from_header_or_query(%s) lookup failed user=%s — fail-closed 403",
                scope,
                user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=missing
            )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=missing
            )
        return user

    _dep.__name__ = f"require_scope_header_or_query[{scope}]"
    _dep.__require_scope__ = scope
    return _dep


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
    _touch_liveness_signal(db, player)
    return player


def _touch_liveness_signal(db: Session, player: Player) -> None:
    """QUEUE-LIVENESS-SIGNAL (2026-07-16): pure post-auth telemetry — writes
    ``Player.last_activity_at`` (NOT ``last_game_login``, see that column's
    own doc-comment on the model) at most once per
    ``_LIVENESS_TOUCH_THROTTLE`` per player, consumed by
    ``presence_helpers._is_presence_fresh`` as a signal that survives an
    entire session with no re-login (the login-route-only
    ``last_game_login`` swept an actively-played JWT-injected seat every
    tick — see that function's own doc-comment for the live repro this
    closes).

    HARD CONSTRAINT: this function runs ONLY after every auth/allow-deny
    decision this dependency chain makes has already happened (both the
    token-validation raises in ``get_current_user`` above it, and this
    function's own caller's not-found raise) — it reads nothing back into
    any conditional and cannot itself raise an HTTPException, so it
    structurally cannot alter an authentication outcome. Any failure here
    (a DB hiccup on the throttled write) is swallowed — a broken activity
    touch must never break a request that would otherwise have succeeded.

    Throttle is free: reads the ``last_activity_at`` already loaded on the
    ``player`` row this dependency just fetched (no extra query), only
    commits when stale — so an active player costs at most one extra
    UPDATE per throttle window, not one per request."""
    try:
        now = datetime.now(timezone.utc)
        last = player.last_activity_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and (now - last) < _LIVENESS_TOUCH_THROTTLE:
            return
        player.last_activity_at = now
        db.commit()
    except Exception:
        logger.debug("liveness-signal touch failed (non-fatal, swallowed)", exc_info=True)
        try:
            db.rollback()
        except Exception:
            logger.debug("liveness-signal touch: rollback also failed", exc_info=True)


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