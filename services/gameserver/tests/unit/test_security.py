"""
Unit tests for security-related functions.
These tests focus on password hashing and verification.
"""
import pytest
from src.core.security import get_password_hash, verify_password
from src.auth.jwt import create_access_token

def test_password_hashing():
    """Test that password hashing works correctly."""
    password = "testpassword123"
    hashed = get_password_hash(password)
    
    # Hash should be different from original password
    assert hashed != password
    
    # Verify should return True for correct password
    assert verify_password(password, hashed) is True
    
    # Verify should return False for incorrect password
    assert verify_password("wrongpassword", hashed) is False

def test_access_token_creation():
    """Test that access token creation function returns expected format."""
    user_id = "test-user-id"

    # Access token
    access_token = create_access_token(user_id)
    assert isinstance(access_token, str)
    assert len(access_token.split(".")) == 3  # JWT format has 3 parts


def test_verify_password_against_known_pre_upgrade_hash():
    """WO-DEPS-86 -- argon2-cffi 23.1.0 -> 25.1.0 back-compat pin.

    Unlike test_password_hashing above (which hashes AND verifies within
    the same argon2-cffi version and would pass even if the wire format
    silently changed across the bump), this fixes the hash as a literal
    minted under argon2-cffi==23.1.0 + passlib==1.7.4 -- the versions
    predating this bump -- so it actually discriminates a format/parameter
    break. Existing player_credentials/admin_credentials password_hash rows
    written before the bump must still verify after it.

    Minted 2026-07-19 in a scratch venv:
        pip install argon2-cffi==23.1.0 passlib==1.7.4
        CryptContext(schemes=["argon2"], deprecated="auto").hash(
            "fixture-password-do-not-reuse"
        )
    """
    known_hash = (
        "$argon2id$v=19$m=65536,t=3,p=4$GwNgLAXgfO99T2mN8f5fyw"
        "$H+JVltJ6J2/T46zSMOOMwcZc7bvsToPelo+nsSZDIoQ"
    )

    assert verify_password("fixture-password-do-not-reuse", known_hash) is True

    # Mutation check: proves the assertion above is actually discriminating
    # (not vacuously true) -- a wrong password against the SAME old hash
    # must still fail.
    assert verify_password("wrong-password", known_hash) is False 