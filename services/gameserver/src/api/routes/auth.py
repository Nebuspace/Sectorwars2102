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

router = APIRouter()


@router.post("/exchange")
async def exchange_oauth_code(code: str = Body(..., embed=True)):
    """Exchange a single-use OAuth authorization code for tokens (ADR-0085).

    The OAuth callback redirects with a short-lived ``code`` (not the tokens);
    the SPA POSTs it here exactly once to receive the JWTs in the response body,
    keeping tokens out of the URL / browser history / referrer / logs.
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

@router.post("/register", response_model=dict)
async def register_user(
    form_data: RegisterForm,
    db: Session = Depends(get_db)
):
    """
    Register a new user with username, email, and password.
    Validates: username (3-30 chars, alphanumeric+underscores), email format, password (min 8 chars).
    """
    username = form_data.username.strip()
    email = form_data.email.strip().lower()
    password = form_data.password

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

    # Find Terran Space by region_type — names are import-specific
    # (BANG imports use "bang-<uuid>-terran_space"), the type is canonical.
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

    # Create Player record with both sector and region assignments
    player = Player(
        user_id=new_user.id,
        nickname=username,
        current_sector_id=starting_sector.sector_id,
        home_sector_id=starting_sector.sector_id,
        current_region_id=terran_space.id,  # Terran Space region UUID
        home_region_id=terran_space.id,     # Terran Space region UUID
        credits=10000  # Starting credits (Terran Space default)
    )
    db.add(player)
    db.commit()
    db.refresh(new_user)

    return {
        "id": str(new_user.id),
        "username": new_user.username,
        "email": new_user.email,
        "is_active": new_user.is_active,
        "is_admin": new_user.is_admin
    }


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
        refresh.revoked = True
        db.commit()

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


# OAuth endpoints
@router.get("/github")
async def login_github(request: Request, register: bool = False):
    """
    Redirect to GitHub OAuth login/registration page.
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()

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
        redirect_uri = f"{base}/auth/github/callback?register={register_param}"
    else:
        # Standard environment handling
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}"

    logger.debug("GitHub OAuth redirect URI configured (env=%s)", settings.detect_environment())

    authorization_url = GitHubOAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("github", authorization_url))


@router.get("/github/callback")
async def github_callback(request: Request, code: str, register: bool = False, state: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Process GitHub OAuth callback.
    """
    # Validate OAuth state parameter (CSRF protection)
    if not _validate_oauth_state(state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired OAuth state. Please try logging in again.",
        )

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
        redirect_uri = f"{base}/auth/github/callback?register={register_param}"
    else:
        # Include the registration flag in the redirect URI
        if api_base_url.endswith(settings.API_V1_STR):
            base = api_base_url
        else:
            base = f"{api_base_url}{settings.API_V1_STR}"

        # Use a literal string rather than letting the bool's repr flow into
        # the URL — gives CodeQL a clean data-flow break (py/url-redirection).
        register_param = "true" if register else "false"
        redirect_uri = f"{base}/auth/github/callback?register={register_param}"

    logger.debug("GitHub OAuth callback URI configured")

    try:
        # Exchange code for token and get user info
        token = await GitHubOAuth.exchange_code_for_token(code, redirect_uri)
        provider_user_id, user_data = await GitHubOAuth.get_user_info(token)

        # Get or create user
        user = await get_oauth_user(db, "github", provider_user_id)
        is_new_user = False

        if not user:
            user = await create_oauth_user(db, "github", provider_user_id, user_data)
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

        # Get the frontend URL for the OAuth callback page
        frontend_base = settings.get_frontend_url()
        frontend_url = f"{frontend_base}/oauth-callback?access_token={access_token}&refresh_token={refresh_token}&user_id={user.id}&is_new_user={is_new_user}"

        logger.debug("OAuth callback: is_new_user=%s, tokens_issued=True", is_new_user)

        # Ensure our redirect is absolute
        if not frontend_url.startswith(('http://', 'https://')):
            logger.warning("Frontend URL is not absolute, attempting fix")
            if settings.detect_environment() == 'codespaces':
                codespace_name = os.environ.get('CODESPACE_NAME', '')
                if codespace_name:
                    frontend_url = f"https://{codespace_name}-3000.app.github.dev/oauth-callback?access_token={access_token}&refresh_token={refresh_token}&user_id={user.id}&is_new_user={is_new_user}"

        return RedirectResponse(frontend_url)

    except Exception as e:
        import traceback
        logger.error("GitHub OAuth error: %s\n%s", str(e), traceback.format_exc())

        # Return generic error — no internal details exposed
        frontend_base = settings.get_frontend_url()
        error_redirect = f"{frontend_base}/login?error=oauth_failed"
        return RedirectResponse(error_redirect)


@router.get("/google")
async def login_google(request: Request, register: bool = False):
    """
    Redirect to Google OAuth login/registration page.
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()

    # Pass the registration flag in the callback URL
    # Remove any duplicate api prefix if present in the base_url
    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/google/callback?register={register_param}"

    logger.debug("Google OAuth redirect URI configured")

    authorization_url = GoogleOAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("google", authorization_url))


@router.get("/google/callback")
async def google_callback(request: Request, code: str, register: bool = False, state: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Process Google OAuth callback.
    """
    # Use the auto-detected API base URL
    api_base_url = settings.get_api_base_url()

    # Include the registration flag in the redirect URI
    # Remove any duplicate api prefix if present in the base_url
    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/google/callback?register={register_param}"

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
        user = await create_oauth_user(db, "google", provider_user_id, user_data)
        is_new_user = True

    # Create tokens and update last login
    access_token, refresh_token = create_tokens(str(user.id), db)
    update_user_last_login(db, str(user.id))

    # Use auto-detected frontend URL
    frontend_base = settings.get_frontend_url()
    frontend_url = f"{frontend_base}/oauth-callback?access_token={access_token}&refresh_token={refresh_token}&user_id={user.id}&is_new_user={is_new_user}"

    return RedirectResponse(frontend_url)


@router.get("/steam")
async def login_steam(request: Request, register: bool = False):
    """
    Redirect to Steam authentication page.
    """
    api_base_url = settings.get_api_base_url()

    if api_base_url.endswith(settings.API_V1_STR):
        base = api_base_url
    else:
        base = f"{api_base_url}{settings.API_V1_STR}"

    register_param = "true" if register else "false"
    redirect_uri = f"{base}/auth/steam/callback?register={register_param}"

    logger.debug("Steam OAuth redirect URI configured")

    authorization_url = SteamAuth.get_authorization_url(redirect_uri)
    return RedirectResponse(_validate_oauth_authorization_url("steam", authorization_url))


@router.get("/steam/callback")
async def steam_callback(request: Request, register: bool = False, db: Session = Depends(get_db)):
    """
    Process Steam authentication callback.
    """
    steam_id = await SteamAuth.verify_response(request)

    logger.debug("Steam authentication callback received")

    # Get Steam user info
    user_data = await SteamAuth.get_user_info(steam_id)

    # Get or create user
    user = await get_oauth_user(db, "steam", steam_id)
    is_new_user = False

    if not user:
        user = await create_oauth_user(db, "steam", steam_id, user_data)
        is_new_user = True

    # Create tokens and update last login
    access_token, refresh_token = create_tokens(str(user.id), db)
    update_user_last_login(db, str(user.id))

    # Use auto-detected frontend URL
    frontend_base = settings.get_frontend_url()
    frontend_url = f"{frontend_base}/oauth-callback?access_token={access_token}&refresh_token={refresh_token}&user_id={user.id}&is_new_user={is_new_user}"

    return RedirectResponse(frontend_url)