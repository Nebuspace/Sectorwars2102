from typing import Dict, Optional, Any, Tuple
import uuid
import time
import logging
import httpx
from fastapi import HTTPException, status, Request
from sqlalchemy.orm import Session
from urllib.parse import urlencode

from src.core.config import settings

logger = logging.getLogger(__name__)

# In-memory OAuth state store with TTL (10 minutes)
# For production multi-instance deployments, replace with Redis
_oauth_states: Dict[str, float] = {}
_OAUTH_STATE_TTL = 600  # 10 minutes


def _generate_oauth_state() -> str:
    """Generate and store an OAuth state parameter for CSRF protection."""
    # Purge expired states
    now = time.monotonic()
    expired = [s for s, t in _oauth_states.items() if now - t > _OAUTH_STATE_TTL]
    for s in expired:
        del _oauth_states[s]

    state = str(uuid.uuid4())
    _oauth_states[state] = now
    return state


def _validate_oauth_state(state: Optional[str]) -> bool:
    """Validate and consume an OAuth state parameter. Returns True if valid."""
    if not state:
        logger.warning("OAuth callback received without state parameter")
        return False
    if state not in _oauth_states:
        logger.warning("OAuth callback received with invalid/expired state: %s", state[:8])
        return False
    # Consume the state (one-time use)
    del _oauth_states[state]
    return True


# ---------------------------------------------------------------------------
# OAuth authorization-code exchange (ADR-0085)
#
# The OAuth callback no longer puts JWTs in the redirect URL (they would land in
# browser history, Referer headers, and URL-capturing logs). Instead it stores
# the freshly-minted tokens here under a short-lived, single-use code and
# redirects with only that code; the SPA POSTs the code to /auth/exchange to
# retrieve the tokens once, over the response body. Codes are single-use and
# expire fast, so a leaked URL is worthless.
#
# In-memory (single-instance) — same caveat as _oauth_states above: replace with
# Redis for multi-instance production.
# ---------------------------------------------------------------------------
_auth_codes: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_AUTH_CODE_TTL = 60  # seconds — the SPA exchanges immediately


def store_auth_code(payload: Dict[str, Any]) -> str:
    """Store token payload under a fresh single-use code; return the code."""
    now = time.monotonic()
    expired = [c for c, (t, _) in _auth_codes.items() if now - t > _AUTH_CODE_TTL]
    for c in expired:
        del _auth_codes[c]

    code = str(uuid.uuid4())
    _auth_codes[code] = (now, payload)
    return code


def consume_auth_code(code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate, consume (one-time), and return the token payload for a code.

    Returns None if the code is missing, unknown, already used, or expired.
    """
    if not code or code not in _auth_codes:
        return None
    issued_at, payload = _auth_codes.pop(code)  # consume regardless of freshness
    if time.monotonic() - issued_at > _AUTH_CODE_TTL:
        logger.warning("Auth code exchange attempted after TTL: %s", code[:8])
        return None
    return payload


from src.models.user import User
from src.models.oauth_account import OAuthAccount
from src.models.player import Player
from src.models.ship import Ship, ShipType


async def get_oauth_user(
    db: Session, 
    provider: str, 
    provider_user_id: str
) -> Optional[User]:
    """
    Get a user by OAuth provider and provider user ID.
    Check for both active and soft-deleted accounts.
    """
    oauth_account = db.query(OAuthAccount).filter(
        OAuthAccount.provider == provider,
        OAuthAccount.provider_user_id == provider_user_id
    ).first()
    
    if oauth_account:
        # If OAuth account is soft-deleted, reactivate it
        if oauth_account.deleted:
            oauth_account.deleted = False
            db.commit()
        
        # Get the associated user (only return if user is active)
        return db.query(User).filter(
            User.id == oauth_account.user_id,
            User.deleted == False
        ).first()
    
    return None


async def create_oauth_user(
    db: Session,
    provider: str,
    provider_user_id: str,
    user_data: Dict[str, Any],
    invite_code: Optional[str] = None,
    ip_hash: Optional[str] = None,
) -> User:
    """
    Create a new user from OAuth data.
    Check if OAuth account already exists and handle appropriately.

    WO-IL6 (AUTH-gated): an OPTIONAL ``invite_code`` places the new OAuth player IN
    the invite's region with instant citizenship (vote-gated by the IL5 60-day
    fence), threaded into the SAME single transaction as the User+OAuthAccount+
    Player+Ship insert (the single ``db.commit()`` below). When the code is ABSENT
    or INVALID, this path is BYTE-FOR-BYTE the existing Terran-Space OAuth signup —
    an invalid code never blocks signup (D10). OAuth parity with /register
    (Review correction / D9). ``invite_code`` only matters when a NEW user is being
    created; for an already-existing OAuth account it is ignored (no re-placement).
    """
    # First check if OAuth account already exists (might be orphaned)
    existing_oauth = db.query(OAuthAccount).filter(
        OAuthAccount.provider == provider,
        OAuthAccount.provider_user_id == provider_user_id
    ).first()
    
    if existing_oauth:
        # OAuth account exists - check if user exists
        if existing_oauth.deleted:
            # Reactivate deleted OAuth account
            existing_oauth.deleted = False
            db.commit()
        
        # Get the associated user
        user = db.query(User).filter(
            User.id == existing_oauth.user_id,
            User.deleted == False
        ).first()
        
        if user:
            return user
        else:
            # OAuth account exists but user is deleted - clean up and recreate
            db.delete(existing_oauth)
            db.commit()
    
    username = user_data.get("username", f"{provider}_{provider_user_id}")
    email = user_data.get("email")
    
    # Create user
    user = User(
        username=username,
        email=email,
        is_active=True,
    )
    db.add(user)
    db.flush()
    
    # Create OAuth account
    oauth_account = OAuthAccount(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        provider_account_email=user_data.get("email"),
        provider_account_username=user_data.get("username"),
        deleted=False
    )
    db.add(oauth_account)

    # Create Player record for OAuth user (with optional invite placement override)
    player = await create_player_for_user(
        db, user, invite_code=invite_code, ip_hash=ip_hash
    )

    db.commit()

    return user


async def create_player_for_user(
    db: Session,
    user: User,
    invite_code: Optional[str] = None,
    ip_hash: Optional[str] = None,
) -> Player:
    """
    Create a Player record for a User (OAuth or otherwise).
    Also creates a starter ship for the player.

    WO-IL6: an OPTIONAL ``invite_code`` overrides the hardcoded Terran-Space
    placement to the invite's region + capital and grants instant citizenship +
    consumes the invite + writes the redemption audit row — all WITHOUT committing
    (the caller, ``create_oauth_user``, owns the single ``db.commit()``). The
    invite row is locked BEFORE the Player rows are created (lock order, brief §5
    Threat 5). Any adverse condition FALLS THROUGH to the unchanged Terran-Space
    default (D10) — never 500, never block signup. When ``invite_code`` is None the
    flow below is byte-for-byte unchanged.
    """
    # Get the Terran Space region and the player's starting sector within it.
    # bang names Region rows "bang-{job_id}-terran_space" and offsets
    # sector_ids globally so terran_space does not start at 1; look up by
    # region_type and use the lowest sector_id in that region. After the
    # translator's _apply_terran_space_invariants, that sector is Sol
    # (Earth Station, security_level=10).
    from src.models.sector import Sector
    from src.models.region import Region, RegionType

    # --- WO-IL6: optional region-invite placement override (OAuth parity / D9) --
    # Lock the invite row FIRST (before the Player rows), re-validate under the
    # lock, and place the OAuth player in the invite's region. Any adverse
    # condition falls through to the unchanged Terran-Space default (D10) — never
    # 500. When invite_code is None, nothing here runs.
    redeem_invite = None
    invite_capital_sector = None
    invite_region_id = None
    if invite_code:
        invite_code = invite_code.strip() or None
    if invite_code:
        from src.auth.region_invite_signup import lock_and_validate_invite
        redeem_invite, invite_capital_sector, _reason = lock_and_validate_invite(
            db, invite_code
        )
        if redeem_invite is not None:
            invite_region_id = redeem_invite.region_id
        else:
            logger.info("oauth signup: invite redeem fell through (%s)", _reason)

    if invite_region_id is not None:
        starting_sector = invite_capital_sector
        target_region_id = invite_region_id
    else:
        # Default (unchanged) Terran-Space path. Standardize on the RegionType
        # enum value (matches /register) rather than the bare string literal so
        # both paths diverge identically if the enum is ever renamed.
        terran_space = (
            db.query(Region)
            .filter(Region.region_type == RegionType.TERRAN_SPACE.value)
            .first()
        )
        if not terran_space:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Terran Space region not found. Galaxy may not be properly initialized."
            )

        starting_sector = (
            db.query(Sector)
            .filter(Sector.region_id == terran_space.id)
            .order_by(Sector.sector_id.asc())
            .first()
        )

        if not starting_sector:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Terran Space has no sectors. Galaxy may not be properly initialized."
            )
        target_region_id = terran_space.id

    starting_sector_id = starting_sector.sector_id

    # Create player with both sector and region assignments
    player = Player(
        user_id=user.id,
        nickname=None,  # Can be set later by user
        credits=10000,  # Starting credits (Terran Space default)
        turns=1000,     # Starting turns
        reputation={},  # Empty reputation
        home_sector_id=starting_sector_id,     # Sol (terran_space's lowest sector_id)
        current_sector_id=starting_sector_id,
        home_region_id=target_region_id,     # invite region or Terran Space
        current_region_id=target_region_id,  # invite region or Terran Space
        is_docked=False,
        is_landed=False,
        team_id=None,
        attack_drones=0,
        defense_drones=0,
        mines=0,
        insurance=None,
        settings={},
        first_login={"completed": False}
    )
    db.add(player)
    db.flush()  # Get the player ID

    # WO-IL6: grant citizenship + consume the invite + write the audit row in THIS
    # open transaction (no commit here — create_oauth_user owns the single commit),
    # while the invite row is still locked. player.id is now real (flushed above).
    if redeem_invite is not None:
        from src.auth.region_invite_signup import finalize_redemption
        finalize_redemption(db, redeem_invite, player.id, ip_hash=ip_hash)

    # Create starter escape pod (as per FIRST_LOGIN.md)
    starter_ship = Ship(
        name="Escape Pod",
        type=ShipType.ESCAPE_POD,  # Start with escape pod
        owner_id=player.id,
        sector_id=starting_sector_id,  # Terran Space's lowest sector_id (Sol)
        cargo={},
        current_speed=1.0,
        base_speed=1.0,
        turn_cost=1,  # Standard turn cost for escape pod
        combat={},
        maintenance={},
        is_flagship=True,
        purchase_value=1000,  # Escape pod value
        current_value=1000
    )
    db.add(starter_ship)
    db.flush()  # Get the ship ID
    
    # Set the starter ship as current ship
    player.current_ship_id = starter_ship.id
    from src.services.ship_service import sync_current_pilot
    sync_current_pilot(player, starter_ship)  # QUEUE-REGISTRY-PILOT-WIRING: no old ship (brand-new player)

    return player


class GitHubOAuth:
    """GitHub OAuth implementation."""

    @staticmethod
    def get_authorization_url(redirect_uri: str) -> str:
        """Get the GitHub authorization URL with CSRF state parameter."""
        params = {
            "client_id": settings.GITHUB_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": _generate_oauth_state(),
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    @staticmethod
    async def exchange_code_for_token(code: str, redirect_uri: str) -> str:
        """Exchange authorization code for access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"}
            )

            data = response.json()
            if "error" in data:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"GitHub auth error: {data.get('error_description', data['error'])}"
                )

            return data.get("access_token")

    @staticmethod
    async def get_user_info(token: str) -> Tuple[str, Dict[str, Any]]:
        """Get user info from GitHub using the access token."""
        async with httpx.AsyncClient() as client:
            # Get user profile
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/json"
                }
            )
            user_data = user_response.json()

            # Get user emails
            email_response = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/json"
                }
            )
            email_data = email_response.json()

            # Find primary email
            primary_email = next(
                (email["email"] for email in email_data if email["primary"]),
                None
            )

            # Extract needed user data
            provider_user_id = str(user_data["id"])
            profile_data = {
                "username": user_data.get("login"),
                "email": primary_email,
                "name": user_data.get("name"),
                "avatar_url": user_data.get("avatar_url"),
                "github_url": user_data.get("html_url"),
                "raw_github_data": user_data
            }

            return provider_user_id, profile_data


class GoogleOAuth:
    """Google OAuth implementation."""

    @staticmethod
    def get_authorization_url(redirect_uri: str) -> str:
        """Get the Google authorization URL with CSRF state parameter."""
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": _generate_oauth_state(),
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    @staticmethod
    async def exchange_code_for_token(code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"}
            )

            data = response.json()
            if "error" in data:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Google auth error: {data.get('error_description', data['error'])}"
                )

            return data

    @staticmethod
    async def get_user_info(token_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Get user info from Google using the ID token."""
        access_token = token_data.get("access_token")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json"
                }
            )
            user_data = response.json()

            # Extract needed user data
            provider_user_id = user_data.get("id")
            profile_data = {
                "username": user_data.get("email").split("@")[0],  # Use email prefix as username
                "email": user_data.get("email"),
                "name": user_data.get("name"),
                "avatar_url": user_data.get("picture"),
                "raw_google_data": user_data
            }

            return provider_user_id, profile_data


class SteamAuth:
    """Steam authentication implementation."""

    @staticmethod
    def get_authorization_url(redirect_uri: str) -> str:
        """Get the Steam authentication URL."""
        # Steam uses OpenID 2.0
        params = {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "checkid_setup",
            "openid.return_to": redirect_uri,
            "openid.realm": redirect_uri.rsplit("/", 1)[0],  # Base URL without path
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        }
        return f"https://steamcommunity.com/openid/login?{urlencode(params)}"

    @staticmethod
    async def verify_response(request: Request) -> str:
        """Verify the Steam OpenID response and extract Steam ID."""
        # Get parameters from the request
        params = dict(request.query_params)

        # Construct verification params
        params["openid.mode"] = "check_authentication"

        async with httpx.AsyncClient() as client:
            # Verify with Steam
            response = await client.post(
                "https://steamcommunity.com/openid/login",
                data=params
            )

            # Check if verification succeeded
            if "is_valid:true" not in response.text:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Steam authentication failed"
                )

            # Extract Steam ID from claimed_id
            claimed_id = params.get("openid.claimed_id", "")
            if not claimed_id or "steamcommunity.com/openid/id/" not in claimed_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Steam ID format"
                )

            steam_id = claimed_id.split("/")[-1]
            return steam_id

    @staticmethod
    async def get_user_info(steam_id: str) -> Dict[str, Any]:
        """Get user info from Steam API using Steam ID."""
        async with httpx.AsyncClient() as client:
            url = (
                f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
                f"?key={settings.STEAM_API_KEY}&steamids={steam_id}"
            )
            response = await client.get(url)
            data = response.json()

            try:
                player = data["response"]["players"][0]
            except (KeyError, IndexError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Steam user not found"
                )

            profile_data = {
                "username": player.get("personaname"),
                "avatar_url": player.get("avatarfull"),
                "steam_url": player.get("profileurl"),
                "raw_steam_data": player
            }

            return profile_data