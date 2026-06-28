from typing import Optional
from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: Optional[str] = None
    exp: Optional[int] = None
    type: Optional[str] = None


class LoginForm(BaseModel):
    username: str = Field(..., description="Admin username")
    password: str = Field(..., description="Admin password")
    mfa_code: Optional[str] = Field(None, description="MFA code (required if MFA is enabled)")


class RegisterForm(BaseModel):
    username: str = Field(..., description="Username (3-30 chars, alphanumeric + underscores)")
    email: str = Field(..., description="Valid email address")
    password: str = Field(..., description="Password (minimum 8 characters)")
    # WO-IL6 — OPTIONAL region-invite code (audit/design-briefs/invite-link-onramp.md
    # §4.6). When ABSENT (None) the registration behaves EXACTLY as before (the
    # Terran-Space default path is byte-for-byte unchanged). When PRESENT and VALID
    # it overrides region placement to the invite's region and grants instant
    # citizenship there (vote-gated by the IL5 60-day account-age fence). An
    # invalid/expired/revoked code falls through to the Terran-Space default with a
    # notice (D10), never a hard reject. Capped to the region_invites.code width.
    invite_code: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Optional region-invite code from a region owner's link",
    )


class RefreshToken(BaseModel):
    refresh_token: str = Field(..., description="Refresh token to get new access token")


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    requires_mfa: Optional[bool] = False
    mfa_enabled: Optional[bool] = False