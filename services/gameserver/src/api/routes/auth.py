import os
import re
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Body
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from jwt import PyJWTError as JWTError

logger = logging.getLogger(__name__)

from src.auth.jwt import create_tokens, decode_token
from src.auth.dependencies import get_current_user
from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.auth.oauth import (
    GitHubOAuth, GoogleOAuth, SteamAuth, get_oauth_user, create_oauth_user,
    _validate_oauth_state, store_auth_code, consume_auth_code,
)
from src.core.database import get_db
from src.core.config import settings
from src.models.refresh_token import RefreshToken
from src.schemas.auth import Token, RefreshToken as RefreshTokenSchema, AuthResponse, LoginForm, RegisterForm
from src.services.user_service import authenticate_admin, authenticate_player, update_user_last_login
from src.services.mfa_service import MFAService
from src.auth.signup_rate_limit import register_rate_limit, exchange_rate_limit

router = APIRouter()


async def _track_player_login(db: Session, user_id) -> None:
    """Record a player-login activity event (best-effort).

    Maps the authenticated User to its Player row (only players have game
    activity; admins are skipped) and fires PlayerActivityService.track_login.
    The activity service is Redis-backed and async; auth routes are async, so
    we await it directly. Fully DEFENSIVE — any failure (no Redis, no Player,
    service error) is swallowed so activity tracking can never break login.
    """
    try:
        from src.models.player import Player
        player = db.query(Player).filter(Player.user_id == user_id).first()
        if player is None:
            return  # admin or non-player user — nothing to track

        # WO-F4 — returning-player welcome-back turn bonus (retention.md). This
        # is the SINGLE shared login chokepoint (every login route funnels here),
        # so the bonus is applied exactly once per login here rather than in each
        # endpoint. Capture the OLD last_game_login BEFORE welcome_back overwrites
        # it to now: that overwrite is what makes the grant one-shot per return
        # (a second login inside 7 days measures a sub-threshold gap → 0). Fully
        # DEFENSIVE — a bonus failure must never break login, so it is isolated
        # in its own try/except and the row is committed best-effort.
        try:
            from src.services.turn_service import welcome_back
            prior_last_game_login = player.last_game_login
            outcome = welcome_back(player, prior_last_game_login)
            db.commit()
            if outcome.get("granted"):
                logger.info(
                    "Welcome-back bonus granted to player %s: +%d turns (%d days inactive)",
                    player.id, outcome.get("bonus", 0), outcome.get("days_inactive", 0),
                )
        except Exception:
            logger.warning("welcome-back turn bonus failed (non-fatal)", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass

        from src.services.player_activity_service import get_player_activity_service
        activity_service = await get_player_activity_service()
        # Call without the optional db arg: the routes' Session is sync, and
        # track_login only uses db to refresh last_game_login (optional). The
        # Redis session/online-set tracking is the part we need here.
        await activity_service.track_login(str(player.id))
    except Exception:
        logger.warning("player-login activity tracking failed (non-fatal)", exc_info=True)


async def _track_player_logout(db: Session, user_id) -> None:
    """Record a player-logout activity event (best-effort).

    Mirror of _track_player_login: maps User -> Player and finalises the
    activity session. Fully DEFENSIVE so it can never break logout.
    """
    try:
        from src.models.player import Player
        player = db.query(Player).filter(Player.user_id == user_id).first()
        if player is None:
            return
        from src.services.player_activity_service import get_player_activity_service
        activity_service = await get_player_activity_service()
        await activity_service.track_logout(str(player.id))
    except Exception:
        logger.warning("player-logout activity tracking failed (non-fatal)", exc_info=True)


@router.post("/exchange", dependencies=[Depends(exchange_rate_limit)])
async def exchange_oauth_code(code: str = Body(..., embed=True)):
    """Exchange a single-use OAuth authorization code for tokens (ADR-0085).

    The OAuth callback redirects with a short-lived ``code`` (not the tokens);
    the SPA POSTs it here exactly once to receive the JWTs in the response body,
    keeping tokens out of the URL / browser history / referrer / logs.

    WO-IL6 (Review correction #1): a per-IP rate limit is applied as a dependency
    so the one-time-code endpoint cannot be hammered. NOTE: /exchange does NOT
    create accounts and carries NO signup params — it only trades an already-
    minted code for tokens. The invite_code is threaded through the OAuth
    *callback* (create_oauth_user), not here.
    """
    payload = consume_auth_code(code)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code",
        )
    return payload


# Allowlist of valid authorization-URL prefixes per OAuth provider. The OAuth
# helper classes construct authorization URLs internally; re-validating them
# here defends against open-redirect even if a forwarded-header value pollutes
# the base URL used during construction (py/url-redirection).
_OAUTH_AUTHORIZATION_PREFIXES = {
    "github": ("https://github.com/login/oauth/authorize",),
    "google": (
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://accounts.google.com/o/oauth2/auth",
    ),
    "steam": ("https://steamcommunity.com/openid/login",),
}


def _validate_oauth_authorization_url(provider: str, url: str) -> str:
    allowed = _OAUTH_AUTHORIZATION_PREFIXES.get(provider, ())
    if not any(url.startswith(p) for p in allowed):
        # Misconfiguration — fail closed rather than redirect somewhere
        # attacker-controlled.
        raise HTTPException(
            status_code=500, detail=f"OAuth provider URL did not match expected prefix for {provider}"
        )
    return url


@router.post("/login", response_model=AuthResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Authenticate admin user with username and password using form data.
    Returns JWT tokens for API access.
    """
    # Get credentials from form data
    username = form_data.username
    password = form_data.password

    # Try to authenticate as admin first, then as player
    user = authenticate_admin(db, username, password)
    if not user:
        # Try player authentication
        from src.services.user_service import authenticate_player
        user = authenticate_player(db, username, password)
        
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new tokens
    access_token, refresh_token = create_tokens(user.id, db)

    # Best-effort player-activity login tracking (no-op for admins)
    await _track_player_login(db, user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": str(user.id)
    }


@router.post("/login/json", response_model=AuthResponse)
async def login_json(
    json_data: LoginForm,
    db: Session = Depends(get_db)
):
    """
    Authenticate admin user with username and password using JSON.
    Returns JWT tokens for API access.
    """
    # Get credentials from JSON data
    username = json_data.username
    password = json_data.password
    
    # Optional debug logging - only for development/testing
    if settings.DEBUG:
        import logging
        logging.debug(f"Login attempt for username: {username}")
        
        # Check if the admin credentials exist in the database for debugging
        admin_user = db.query(User).filter(User.username == username, User.is_admin == True).first()
        if admin_user and settings.DEBUG:
            logging.debug(f"Admin user found in database with ID: {admin_user.id}")
            admin_creds = db.query(AdminCredentials).filter(AdminCredentials.user_id == admin_user.id).first()
            if admin_creds:
                logging.debug("Admin credentials found in database")
            else:
                logging.debug("Admin user exists but no credentials record found")
        elif settings.DEBUG:
            logging.debug(f"Admin user '{username}' not found in database")
    
    # Try to authenticate as admin first, then as player
    user = authenticate_admin(db, username, password)
    if not user:
        # Try player authentication
        from src.services.user_service import authenticate_player
        user = authenticate_player(db, username, password)
        
    if not user:
        if settings.DEBUG:
            import logging
            logging.error("Authentication failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new tokens
    access_token, refresh_token = create_tokens(user.id, db)

    # Best-effort player-activity login tracking (no-op for admins)
    await _track_player_login(db, user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": str(user.id)
    }

@router.options("/login/json")
async def options_login_json():
    """
    Handle preflight CORS requests for login/json endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }

@router.options("/login")
async def options_login():
    """
    Handle preflight CORS requests for login endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }

@router.post("/login/direct", response_model=AuthResponse)
async def login_direct(
    json_data: LoginForm,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Direct authentication endpoint with MFA support.
    This endpoint supports both regular login and MFA verification.
    Accepts JSON body with username, password, and optional MFA code.
    """
    # Get credentials from JSON data
    username = json_data.username
    password = json_data.password
    mfa_code = json_data.mfa_code
    
    # Try to authenticate as admin first, then as player
    user = authenticate_admin(db, username, password)
    if not user:
        # Try player authentication
        from src.services.user_service import authenticate_player
        user = authenticate_player(db, username, password)
        
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Check if MFA is enabled for this user
    try:
        mfa_service = MFAService(db)
        mfa_enabled = mfa_service.is_mfa_enabled(str(user.id))
    except Exception as e:
        # MFA table might not exist yet, disable MFA for now
        logger.warning("MFA service error (table may not exist): %s", e)
        # Rollback the current transaction to avoid transaction errors
        db.rollback()
        mfa_enabled = False
    
    if mfa_enabled:
        # MFA is enabled, check if code was provided
        if not mfa_code:
            # Return response indicating MFA is required
            return {
                "access_token": "",
                "refresh_token": "",
                "token_type": "bearer",
                "user_id": str(user.id),
                "requires_mfa": True,
                "mfa_enabled": True
            }
        
        # Verify MFA code
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        mfa_valid = mfa_service.verify_code(
            str(user.id), 
            mfa_code,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        if not mfa_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    # Authentication successful (with or without MFA)
    access_token, refresh_token = create_tokens(user.id, db)

    # Best-effort player-activity login tracking (no-op for admins)
    await _track_player_login(db, user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": str(user.id),
        "requires_mfa": False,
        "mfa_enabled": mfa_enabled
    }


@router.post("/player/login", response_model=AuthResponse)
async def player_login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Authenticate player user with username and password using form data.
    Returns JWT tokens for API access.
    """
    # Get credentials from form data
    username = form_data.username
    password = form_data.password

    # Authenticate user
    user = authenticate_player(db, username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new tokens
    access_token, refresh_token = create_tokens(user.id, db)

    # Best-effort player-activity login tracking
    await _track_player_login(db, user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": str(user.id)
    }


@router.post("/player/login/json", response_model=AuthResponse)
async def player_login_json(
    json_data: LoginForm,
    db: Session = Depends(get_db)
):
    """
    Authenticate player user with username and password using JSON.
    Returns JWT tokens for API access.
    """
    # Get credentials from JSON data
    username = json_data.username
    password = json_data.password

    # Authenticate user
    user = authenticate_player(db, username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new tokens
    access_token, refresh_token = create_tokens(user.id, db)

    # Best-effort player-activity login tracking
    await _track_player_login(db, user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": str(user.id)
    }

@router.options("/player/login/json")
async def options_player_login_json():
    """
    Handle preflight CORS requests for player login/json endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }

@router.options("/player/login")
async def options_player_login():
    """
    Handle preflight CORS requests for player login endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }

@router.post("/register", response_model=dict, dependencies=[Depends(register_rate_limit)])
async def register_user(
    form_data: RegisterForm,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Register a new user with username, email, and password.
    Validates: username (3-30 chars, alphanumeric+underscores), email format, password (min 8 chars).

    WO-IL6 (AUTH-gated): an OPTIONAL ``invite_code`` (RegisterForm) places a valid
    new account IN the invite's region with instant citizenship (vote-gated by the
    IL5 60-day account-age fence). When the code is ABSENT or INVALID, this path is
    BYTE-FOR-BYTE the existing Terran-Space signup (the invalid case adds only a
    ``redemption_notice`` to the response; an invalid code never blocks signup — D10).
    """
    username = form_data.username.strip()
    email = form_data.email.strip().lower()
    password = form_data.password
    invite_code = (form_data.invite_code or "").strip() or None

    # --- Input validation (GAMMA-001, GAMMA-002) ---
    errors = []
    if not re.match(r"^[a-zA-Z0-9_]{3,30}$", username):
        errors.append(
            "Username must be 3-30 characters and contain only letters, numbers, and underscores"
        )
    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        errors.append("Invalid email format")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors
        )

    # Check if username or email already exists
    existing_user = db.query(User).filter(
        (User.username == username) | (User.email == email),
        User.deleted == False
    ).first()

    if existing_user:
        if existing_user.username == username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists"
            )

    # Create new user
    from src.core.security import get_password_hash
    from src.models.player_credentials import PlayerCredentials

    new_user = User(
        username=username,
        email=email,
        is_active=True,
        is_admin=False
    )

    # Flush (not commit) so new_user.id exists for the FK rows below while
    # keeping the whole registration atomic — a failure past this point must
    # not leave an orphaned User with no Player (login succeeds, game 422s).
    db.add(new_user)
    db.flush()

    # Create player credentials
    player_creds = PlayerCredentials(
        user_id=new_user.id,
        password_hash=get_password_hash(password)
    )
    db.add(player_creds)

    from src.models.sector import Sector
    from src.models.player import Player
    from src.models.region import Region, RegionType

    # --- WO-IL6: optional region-invite placement override ---------------------
    # If a VALID invite is supplied, lock its row FIRST (lock order: invite BEFORE
    # the Player rows — brief §5 Threat 5), re-validate under the lock, and place
    # the account in the invite's region instead of Terran Space. Any adverse
    # condition (bad/expired/revoked code, region gone/closed/no-sectors, owner
    # changed) FALLS THROUGH to the existing Terran-Space default + a notice — it
    # NEVER 500s and NEVER blocks signup (D10 / Review correction #4/#5). When no
    # code is supplied, nothing here runs and the path below is unchanged.
    redeem_invite = None
    invite_capital_sector = None
    invite_region_id = None
    redemption_notice = None
    if invite_code:
        from src.auth.region_invite_signup import (
            lock_and_validate_invite,
            NOTICE_INVITE_INVALID,
        )
        redeem_invite, invite_capital_sector, _reason = lock_and_validate_invite(
            db, invite_code
        )
        if redeem_invite is not None:
            invite_region_id = redeem_invite.region_id
        else:
            # D10 fall-through: keep the account, place in Terran Space, surface a
            # generic notice (do not leak which adverse condition tripped).
            redemption_notice = NOTICE_INVITE_INVALID
            logger.info("register: invite redeem fell through (%s)", _reason)

    if invite_region_id is not None:
        # Invite path — placement overridden to the invite's region + capital.
        starting_sector = invite_capital_sector
        target_region_id = invite_region_id
    else:
        # Default (unchanged) Terran-Space path. Find Terran Space by region_type
        # — names are import-specific (BANG imports use "bang-<uuid>-terran_space"),
        # the type is canonical.
        terran_space = db.query(Region).filter(
            Region.region_type == RegionType.TERRAN_SPACE.value
        ).first()
        if not terran_space:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Terran Space region not found. Galaxy may not be properly initialized."
            )

        # Capital Sector per ADR-0005 is sector 1 for Terran Space, but
        # Region.capital_sector_number doesn't exist yet and BANG imports number
        # sectors globally (Terran = 1001+). The region's lowest sector_number is
        # the capital in both layouts.
        starting_sector = db.query(Sector).filter(
            Sector.region_id == terran_space.id
        ).order_by(Sector.sector_id.asc()).first()

        if not starting_sector:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No sectors found in Terran Space. Galaxy may not be properly initialized."
            )
        target_region_id = terran_space.id

    # Create Player record with both sector and region assignments
    player = Player(
        user_id=new_user.id,
        nickname=username,
        current_sector_id=starting_sector.sector_id,
        home_sector_id=starting_sector.sector_id,
        current_region_id=target_region_id,  # invite region or Terran Space
        home_region_id=target_region_id,     # invite region or Terran Space
        credits=10000  # Starting credits (Terran Space default)
    )
    db.add(player)

    # WO-IL6: grant citizenship + consume the invite + write the audit row — all
    # in THIS open transaction, while the invite row is still locked. Flush first
    # so player.id is real for the membership + audit FKs. A failure anywhere
    # before the single commit below rolls back account + membership + use +
    # audit together (brief §4.6-8 atomicity).
    if redeem_invite is not None:
        from src.auth.region_invite_signup import finalize_redemption, hash_ip
        db.flush()  # materialize player.id for the citizenship + redemption FKs
        client_host = request.client.host if request and request.client else None
        finalize_redemption(
            db,
            redeem_invite,
            player.id,
            ip_hash=hash_ip(client_host),
        )

    db.commit()
    db.refresh(new_user)

    response = {
        "id": str(new_user.id),
        "username": new_user.username,
        "email": new_user.email,
        "is_active": new_user.is_active,
        "is_admin": new_user.is_admin
    }
    # Only present when an invite code was supplied but did not redeem (D10). The
    # happy no-invite path is byte-for-byte unchanged (no extra key).
    if redemption_notice is not None:
        response["redemption_notice"] = redemption_notice
    return response


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    token_data: RefreshTokenSchema,
    db: Session = Depends(get_db)
):
    """
    Get a new access token using a refresh token.
    Implements refresh token rotation for security.
    """
    refresh = db.query(RefreshToken).filter(
        RefreshToken.token == token_data.refresh_token, 
        RefreshToken.revoked == False
    ).first()
    
    if not refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    
    # Check if token has expired
    if refresh.is_expired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
        )
    
    # Revoke the current refresh token (token rotation)
    refresh.revoked = True
    db.commit()
    
    # Create new tokens
    access_token, new_refresh_token = create_tokens(str(refresh.user_id), db)
    update_user_last_login(db, str(refresh.user_id))
    
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user_id": str(refresh.user_id)
    }


@router.post("/logout")
async def logout(
    token_data: RefreshTokenSchema,
    db: Session = Depends(get_db)
):
    """
    Revoke a refresh token, effectively logging the user out.
    """
    refresh = db.query(RefreshToken).filter(
        RefreshToken.token == token_data.refresh_token,
        RefreshToken.revoked == False
    ).first()

    if refresh:
        revoked_user_id = refresh.user_id
        refresh.revoked = True
        db.commit()
        # Best-effort player-activity logout tracking (finalises the session)
        await _track_player_logout(db, revoked_user_id)

    return {"detail": "Successfully logged out"}


@router.get("/me")
async def get_current_user_info(current_user = Depends(get_current_user)):
    """
    Get information about the currently authenticated user.
    """
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "last_login": current_user.last_login
    }

@router.options("/me")
async def options_me():
    """
    Handle preflight CORS requests for the /me endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }

@router.post("/me/token", response_model=dict)
async def get_user_by_token(
    token: str = Body(...),
    db: Session = Depends(get_db)
):
    """
    Alternative endpoint to get user info by providing the token directly in the request body.
    This avoids CORS preflight issues with the Authorization header.
    """
    try:
        # Decode token
        payload = decode_token(token)
        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        # Get user from database
        user = db.query(User).filter(User.id == user_id, User.deleted == False).first()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        # Return user info
        return {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "is_admin": user.is_admin,
            "is_active": user.is_active,
            "last_login": user.last_login
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate token",
        )


# WO-IL6: sanitize an OAuth-carried invite code so a hostile query value can never
# break out of the callback redirect URI (the code travels in redirect_uri to the
# provider and back). region_invites.code is secrets.token_urlsafe(16) -> base64url
# ([A-Za-z0-9_-]); anything outside that alphabet, or over the column width, is
# dropped (treated as "no invite"). Codes are shareable links, NOT secrets, so
# carrying them in the URL is by design — but they must be inert as URL data.
import re as _re_invite

def _sanitize_oauth_invite(invite: Optional[str]) -> Optional[str]:
    if not invite:
        return None
    invite = invite.strip()
    if not invite or len(invite) > 64:
        return None
    if not _re_invite.match(r"^[A-Za-z0-9_-]+$", invite):
        return None
    return invite


def _invite_query_suffix(invite: Optional[str]) -> str:
    """``&invite=<code>`` for a sanitized code, else ``''`` (default flow byte-
    for-byte unchanged when no/invalid invite is present)."""
    return f"&invite={invite}" if invite else ""


# OAuth endpoints
@router.get("/github")
async def login_github(request: Request, register: bool = False, invite: Optional[str] = None):
    """
    Redirect to GitHub OAuth login/registration page.

    WO-IL6: an OPTIONAL ``invite`` query param (a region-invite code) is carried
    through the OAuth round-trip in the callback redirect URI so a new OAuth player
    can be placed in the invite's region. The code is sanitized (URL-inert) first;
    an absent/invalid code leaves the redirect URI unchanged.
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()
    invite = _sanitize_oauth_invite(invite)

    # For GitHub Codespaces, ALWAYS use the API_BASE_URL from settings
    # This ensures we use the proper public URL without any port numbers
    if settings.detect_environment() == "codespaces":
        # In Codespaces, we shouldn't rely on host headers - instead use the
        # configured API_BASE_URL that includes the Codespace name
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Always use the API_BASE_URL setting for Codespaces
        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}{_invite_query_suffix(invite)}"
    else:
        # Standard environment handling
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}{_invite_query_suffix(invite)}"

    logger.debug("GitHub OAuth redirect URI configured (env=%s)", settings.detect_environment())

    authorization_url = GitHubOAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("github", authorization_url))


@router.get("/github/callback")
async def github_callback(request: Request, code: str, register: bool = False, state: Optional[str] = None, invite: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Process GitHub OAuth callback.
    """
    # Validate OAuth state parameter (CSRF protection)
    if not _validate_oauth_state(state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired OAuth state. Please try logging in again.",
        )

    # WO-IL6: sanitize the carried invite (URL-inert) before it re-enters the
    # redirect_uri (which must byte-match the one sent to GitHub for the exchange).
    invite = _sanitize_oauth_invite(invite)

    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()

    # Get the actual request URL used to access this endpoint
    callback_url = str(request.url)

    # Detailed debug for the callback
    logger.debug("GitHub OAuth callback received (env=%s)", settings.detect_environment())

    # For Codespaces, use the same URL that was used in the initial request
    if settings.detect_environment() == "codespaces":
        # In Codespaces, we shouldn't rely on host headers - instead use the
        # configured API_BASE_URL that includes the Codespace name
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Always use the API_BASE_URL setting for Codespaces
        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}{_invite_query_suffix(invite)}"
    else:
        # Include the registration flag in the redirect URI
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}{_invite_query_suffix(invite)}"

    logger.debug("GitHub OAuth callback URI configured")

    try:
        # Exchange code for token and get user info
        token = await GitHubOAuth.exchange_code_for_token(code, redirect_uri)
        provider_user_id, user_data = await GitHubOAuth.get_user_info(token)

        # Get or create user
        user = await get_oauth_user(db, "github", provider_user_id)
        is_new_user = False

        if not user:
            # WO-IL6: thread the (sanitized) invite into the SINGLE create txn.
            from src.auth.region_invite_signup import hash_ip
            _ch = request.client.host if request and request.client else None
            user = await create_oauth_user(
                db, "github", provider_user_id, user_data,
                invite_code=invite, ip_hash=hash_ip(_ch),
            )
            is_new_user = True
        else:
            # Check if existing user has a Player record
            from src.models.player import Player
            existing_player = db.query(Player).filter(Player.user_id == user.id).first()
            if not existing_player:
                # Existing OAuth user without Player record - create one
                from src.auth.oauth import create_player_for_user
                await create_player_for_user(db, user)
                db.commit()
                logger.info("Created Player record for existing OAuth user: %s", user.username)

        # Create tokens and update last login
        access_token, refresh_token = create_tokens(str(user.id), db)
        update_user_last_login(db, str(user.id))

        # ADR-0085: keep tokens OUT of the redirect URL. Stash the token payload
        # server-side under a short-lived single-use code and redirect with only
        # the code (+ user_id / is_new_user, which are not secrets). The SPA POSTs
        # the code to /auth/exchange exactly once to retrieve the tokens in the
        # response body, so they never land in history / Referer / logs.
        auth_code = store_auth_code({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user_id": str(user.id),
            "is_new_user": is_new_user,
        })

        # Get the frontend URL for the OAuth callback page
        frontend_base = settings.get_frontend_url()
        frontend_url = f"{frontend_base}/oauth-callback?code={auth_code}&user_id={user.id}&is_new_user={is_new_user}"

        logger.debug("OAuth callback: is_new_user=%s, code_issued=True", is_new_user)

        # Ensure our redirect is absolute
        if not frontend_url.startswith(('http://', 'https://')):
            logger.warning("Frontend URL is not absolute, attempting fix")
            if settings.detect_environment() == 'codespaces':
                codespace_name = os.environ.get('CODESPACE_NAME', '')
                if codespace_name:
                    frontend_url = f"https://{codespace_name}-3000.app.github.dev/oauth-callback?code={auth_code}&user_id={user.id}&is_new_user={is_new_user}"

        return RedirectResponse(frontend_url)

    except Exception as e:
        import traceback
        logger.error("GitHub OAuth error: %s\n%s", str(e), traceback.format_exc())

        # Return generic error — no internal details exposed
        frontend_base = settings.get_frontend_url()
        error_redirect = f"{frontend_base}/login?error=oauth_failed"
        return RedirectResponse(error_redirect)


@router.get("/google")
async def login_google(request: Request, register: bool = False, invite: Optional[str] = None):
    """
    Redirect to Google OAuth login/registration page.

    WO-IL6: optional ``invite`` carried through the round-trip (OAuth parity / D9).
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()
    invite = _sanitize_oauth_invite(invite)

    # Pass the registration flag in the callback URL
    # Remove any duplicate api prefix if present in the base_url
    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/google/callback?register={register_param}{_invite_query_suffix(invite)}"

    logger.debug("Google OAuth redirect URI configured")

    authorization_url = GoogleOAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("google", authorization_url))


@router.get("/google/callback")
async def google_callback(request: Request, code: str, register: bool = False, state: Optional[str] = None, invite: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Process Google OAuth callback.
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()
    # WO-IL6: sanitize before rebuilding the (byte-matching) redirect_uri.
    invite = _sanitize_oauth_invite(invite)

    # Include the registration flag in the redirect URI
    # Remove any duplicate api prefix if present in the base_url
    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/google/callback?register={register_param}{_invite_query_suffix(invite)}"

    # Validate OAuth state parameter (CSRF protection)
    if not _validate_oauth_state(state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired OAuth state. Please try logging in again.",
        )

    logger.debug("Google OAuth callback URI configured")

    # Exchange code for tokens and get user info
    token_data = await GoogleOAuth.exchange_code_for_token(code, redirect_uri)
    provider_user_id, user_data = await GoogleOAuth.get_user_info(token_data)

    # Get or create user
    user = await get_oauth_user(db, "google", provider_user_id)
    is_new_user = False

    if not user:
        from src.auth.region_invite_signup import hash_ip
        _ch = request.client.host if request and request.client else None
        user = await create_oauth_user(
            db, "google", provider_user_id, user_data,
            invite_code=invite, ip_hash=hash_ip(_ch),
        )
        is_new_user = True

    # Create tokens and update last login
    access_token, refresh_token = create_tokens(str(user.id), db)
    update_user_last_login(db, str(user.id))

    # ADR-0085: tokens go server-side under a single-use code, not in the URL.
    auth_code = store_auth_code({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": str(user.id),
        "is_new_user": is_new_user,
    })

    # Use auto-detected frontend URL
    frontend_base = settings.get_frontend_url()
    frontend_url = f"{frontend_base}/oauth-callback?code={auth_code}&user_id={user.id}&is_new_user={is_new_user}"

    return RedirectResponse(frontend_url)


@router.get("/steam")
async def login_steam(request: Request, register: bool = False, invite: Optional[str] = None):
    """
    Redirect to Steam authentication page.

    WO-IL6: optional ``invite`` carried in the OpenID return_to (OAuth parity / D9).
    """
    api_base_url = settings.get_api_base_url()
    invite = _sanitize_oauth_invite(invite)

    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/steam/callback?register={register_param}{_invite_query_suffix(invite)}"

    logger.debug("Steam OAuth redirect URI configured")

    authorization_url = SteamAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("steam", authorization_url))


@router.get("/steam/callback")
async def steam_callback(request: Request, register: bool = False, invite: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Process Steam authentication callback.
    """
    steam_id = await SteamAuth.verify_response(request)

    logger.debug("Steam authentication callback received")

    # WO-IL6: sanitize the carried invite before use.
    invite = _sanitize_oauth_invite(invite)

    # Get Steam user info
    user_data = await SteamAuth.get_user_info(steam_id)

    # Get or create user
    user = await get_oauth_user(db, "steam", steam_id)
    is_new_user = False

    if not user:
        from src.auth.region_invite_signup import hash_ip
        _ch = request.client.host if request and request.client else None
        user = await create_oauth_user(
            db, "steam", steam_id, user_data,
            invite_code=invite, ip_hash=hash_ip(_ch),
        )
        is_new_user = True

    # Create tokens and update last login
    access_token, refresh_token = create_tokens(str(user.id), db)
    update_user_last_login(db, str(user.id))

    # ADR-0085: tokens go server-side under a single-use code, not in the URL.
    auth_code = store_auth_code({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": str(user.id),
        "is_new_user": is_new_user,
    })

    # Use auto-detected frontend URL
    frontend_base = settings.get_frontend_url()
    frontend_url = f"{frontend_base}/oauth-callback?code={auth_code}&user_id={user.id}&is_new_user={is_new_user}"

    return RedirectResponse(frontend_url)