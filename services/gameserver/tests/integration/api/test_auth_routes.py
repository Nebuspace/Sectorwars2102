from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.user import User
from src.models.refresh_token import RefreshToken as RefreshTokenModel  # Alias to avoid conflict

# Tests for POST /api/v1/auth/login/json
def test_login_json_success(client: TestClient, db: Session):
    response = client.post(
        f"{settings.API_V1_STR}/auth/login/json",
        json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "user_id" in data
    admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    assert admin_user is not None
    assert str(admin_user.id) == data["user_id"]

# Test with wrong password
def test_login_json_failure_wrong_password(client: TestClient):
    response = client.post(
        f"{settings.API_V1_STR}/auth/login/json",
        json={"username": settings.ADMIN_USERNAME, "password": "wrongpassword"}
    )
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Incorrect username or password"}

# Test with wrong username
def test_login_json_failure_wrong_username(client: TestClient):
    response = client.post(
        f"{settings.API_V1_STR}/auth/login/json",
        json={"username": "nonexistentadmin", "password": settings.ADMIN_PASSWORD}
    )
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Incorrect username or password"}

# Tests for POST /api/v1/auth/refresh
def test_refresh_token_success(client: TestClient, db: Session):
    login_response = client.post(
        f"{settings.API_V1_STR}/auth/login/json",
        json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD}
    )
    login_data = login_response.json()
    refresh_token_value = login_data["refresh_token"]
    original_user_id = login_data["user_id"]

    refresh_response = client.post(
        f"{settings.API_V1_STR}/auth/refresh",
        json={"refresh_token": refresh_token_value}
    )
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_data = refresh_response.json()
    assert "access_token" in refresh_data
    assert "refresh_token" in refresh_data
    assert refresh_data["token_type"] == "bearer"
    assert refresh_data["user_id"] == original_user_id
    assert refresh_data["refresh_token"] != refresh_token_value  # Token rotation

def test_refresh_token_failure_invalid_token(client: TestClient):
    response = client.post(
        f"{settings.API_V1_STR}/auth/refresh",
        json={"refresh_token": "invalid_dummy_token"}
    )
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Invalid refresh token"}

# Tests for POST /api/v1/auth/logout
def test_logout_success(client: TestClient, db: Session):
    login_response = client.post(
        f"{settings.API_V1_STR}/auth/login/json",
        json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD}
    )
    refresh_token_value = login_response.json()["refresh_token"]

    logout_response = client.post(
        f"{settings.API_V1_STR}/auth/logout",
        json={"refresh_token": refresh_token_value}
    )
    assert logout_response.status_code == 200, logout_response.text
    assert logout_response.json() == {"detail": "Successfully logged out"}

    # Verify the token is revoked in the database
    revoked_token = db.query(RefreshTokenModel).filter(RefreshTokenModel.token == refresh_token_value).first()
    assert revoked_token is not None
    assert revoked_token.revoked is True

    refresh_after_logout_response = client.post(
        f"{settings.API_V1_STR}/auth/refresh",
        json={"refresh_token": refresh_token_value}
    )
    assert refresh_after_logout_response.status_code == 401, refresh_after_logout_response.text
    assert refresh_after_logout_response.json() == {"detail": "Invalid refresh token"}

# Tests for GET /api/v1/auth/me
def test_get_me_success(client: TestClient, admin_auth_headers: dict, db: Session):
    response = client.get(f"{settings.API_V1_STR}/auth/me", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    assert data["id"] == str(admin_user.id)
    assert data["username"] == settings.ADMIN_USERNAME
    assert data["email"] == admin_user.email
    assert data["is_admin"] is True

def test_get_me_failure_no_auth(client: TestClient):
    response = client.get(f"{settings.API_V1_STR}/auth/me")
    assert response.status_code == 401, response.text  # FastAPI default for missing auth
    assert response.json() == {"detail": "Not authenticated"}

# Tests for POST /api/v1/auth/me/token
def test_get_user_by_token_success(client: TestClient, admin_auth_headers: dict, db: Session):
    # Extract token from admin_auth_headers
    token = admin_auth_headers["Authorization"].split("Bearer ")[1]

    response = client.post(f"{settings.API_V1_STR}/auth/me/token", json=token)
    assert response.status_code == 200, response.text
    data = response.json()
    admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
    assert data["id"] == str(admin_user.id)
    assert data["username"] == settings.ADMIN_USERNAME
    assert data["email"] == admin_user.email
    assert data["is_admin"] is True

def test_get_user_by_token_failure_invalid_token(client: TestClient):
    response = client.post(f"{settings.API_V1_STR}/auth/me/token", json="invalid.jwt.token")
    assert response.status_code == 401, response.text
    # The detail might vary slightly based on JWTError handling
    assert "Could not validate token" in response.json()["detail"] or "Invalid token" in response.json()["detail"]

# OAuth endpoints (/api/v1/auth/github, /api/v1/auth/github/callback, etc.)
# are harder to test directly in unit/integration tests as they involve redirects
# and external provider interactions. These are typically better suited for E2E tests
# or require significant mocking of the external OAuth flow.
# For now, these will be skipped in this unit test file.
