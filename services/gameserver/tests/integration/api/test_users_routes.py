# Tests for user-related API routes
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import uuid

from src.core.config import settings
from src.models.user import User

# Helper to create a unique user for testing to avoid conflicts
def create_unique_user_data(is_admin: bool = False):
    unique_suffix = uuid.uuid4().hex[:6]
    data = {
        "username": f"testuser_{unique_suffix}",
        "email": f"test_{unique_suffix}@example.com",
    }
    if is_admin:
        data["password"] = "securepassword123"
    return data

# Tests for GET /api/v1/users/
def test_read_users_success(client: TestClient, admin_auth_headers: dict, db: Session):
    response = client.get(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    # Further checks can be added, e.g., if the default admin is in the list

def test_read_users_unauthorized(client: TestClient):
    response = client.get(f"{settings.API_V1_STR}/users/")
    assert response.status_code == 401, response.text # Expect 401 for missing auth
    assert response.json() == {"detail": "Not authenticated"}

# Tests for POST /api/v1/users/
# This endpoint seems to be for creating regular users by an admin
def test_create_user_success(client: TestClient, admin_auth_headers: dict, db: Session):
    user_payload = create_unique_user_data()
    response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=user_payload)
    assert response.status_code == 200, response.text # Assuming 200 or 201 for creation
    data = response.json()
    assert data["username"] == user_payload["username"]
    assert data["email"] == user_payload["email"]
    assert data["is_admin"] is False
    # Verify in DB
    user_in_db = db.query(User).filter(User.username == user_payload["username"]).first()
    assert user_in_db is not None
    assert user_in_db.email == user_payload["email"]

def test_create_user_failure_username_exists(client: TestClient, admin_auth_headers: dict):
    user_payload = {"username": settings.ADMIN_USERNAME, "email": "unique_email@example.com"}
    response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=user_payload)
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Username already registered"}

# Tests for POST /api/v1/users/admin
def test_create_admin_user_success(client: TestClient, admin_auth_headers: dict, db: Session):
    admin_payload = create_unique_user_data(is_admin=True)
    response = client.post(f"{settings.API_V1_STR}/users/admin", headers=admin_auth_headers, json=admin_payload)
    assert response.status_code == 200, response.text # Assuming 200 or 201
    data = response.json()
    assert data["username"] == admin_payload["username"]
    assert data["email"] == admin_payload["email"]
    assert data["is_admin"] is True
    # Verify in DB
    user_in_db = db.query(User).filter(User.username == admin_payload["username"]).first()
    assert user_in_db is not None
    assert user_in_db.is_admin is True

# Tests for GET /api/v1/users/{user_id}
def test_read_user_success(client: TestClient, admin_auth_headers: dict, db: Session):
    # Create a user first to ensure one exists to be read (besides default admin)
    user_data = create_unique_user_data()
    created_user_response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=user_data)
    created_user_id = created_user_response.json()["id"]

    response = client.get(f"{settings.API_V1_STR}/users/{created_user_id}", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"] == created_user_id
    assert data["username"] == user_data["username"]

def test_read_user_not_found(client: TestClient, admin_auth_headers: dict):
    non_existent_uuid = uuid.uuid4()
    response = client.get(f"{settings.API_V1_STR}/users/{non_existent_uuid}", headers=admin_auth_headers)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "User not found"}

# Tests for PUT /api/v1/users/{user_id}
def test_update_user_success(client: TestClient, admin_auth_headers: dict, db: Session):
    user_data = create_unique_user_data()
    created_user_response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=user_data)
    user_id_to_update = created_user_response.json()["id"]

    update_payload = {"username": f"updated_{user_data['username']}", "email": f"updated_{user_data['email']}", "is_active": False}
    response = client.put(f"{settings.API_V1_STR}/users/{user_id_to_update}", headers=admin_auth_headers, json=update_payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["username"] == update_payload["username"]
    assert data["email"] == update_payload["email"]
    assert data["is_active"] is False

# Tests for DELETE /api/v1/users/{user_id}
def test_delete_user_success(client: TestClient, admin_auth_headers: dict, db: Session):
    user_data = create_unique_user_data()
    created_user_response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=user_data)
    user_id_to_delete = created_user_response.json()["id"]

    response = client.delete(f"{settings.API_V1_STR}/users/{user_id_to_delete}", headers=admin_auth_headers)
    assert response.status_code == 200, response.text # Assuming 200 for soft delete
    data = response.json()
    assert data["deleted"] is True # Check soft delete flag

    # Verify user is marked as deleted in DB
    deleted_user_in_db = db.query(User).filter(User.id == user_id_to_delete).first()
    assert deleted_user_in_db is not None
    assert deleted_user_in_db.deleted is True

def test_delete_self_failure(client: TestClient, admin_auth_headers: dict, db: Session):
    # Get current admin user ID from token
    me_response = client.get(f"{settings.API_V1_STR}/auth/me", headers=admin_auth_headers)
    admin_user_id = me_response.json()["id"]

    response = client.delete(f"{settings.API_V1_STR}/users/{admin_user_id}", headers=admin_auth_headers)
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Cannot delete your own account"}

# Tests for PUT /api/v1/users/{user_id}/password
def test_reset_admin_password_success(client: TestClient, admin_auth_headers: dict, db: Session):
    # Create a new admin user to safely test password reset on
    new_admin_payload = create_unique_user_data(is_admin=True)
    created_admin_response = client.post(f"{settings.API_V1_STR}/users/admin", headers=admin_auth_headers, json=new_admin_payload)
    assert created_admin_response.status_code == 200, created_admin_response.text
    admin_id_to_reset = created_admin_response.json()["id"]

    new_password = "newStrongPassword123!"
    response = client.put(
        f"{settings.API_V1_STR}/users/{admin_id_to_reset}/password", 
        headers=admin_auth_headers, 
        json=new_password # FastAPI reads simple string body for `password: str = Body(...)`
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"detail": "Password updated successfully"}

    # Optional: Verify login with new password (requires AdminCredentials model access or another endpoint)
    # This might be complex if AdminCredentials is not directly queryable or if hashing makes direct check hard.

def test_reset_admin_password_user_not_admin(client: TestClient, admin_auth_headers: dict, db: Session):
    # Create a regular user
    reg_user_payload = create_unique_user_data()
    created_user_response = client.post(f"{settings.API_V1_STR}/users/", headers=admin_auth_headers, json=reg_user_payload)
    user_id = created_user_response.json()["id"]

    response = client.put(
        f"{settings.API_V1_STR}/users/{user_id}/password", 
        headers=admin_auth_headers, 
        json="newpassword123"
    )
    assert response.status_code == 404, response.text # Endpoint expects an admin user
    assert response.json() == {"detail": "Admin user not found"}
